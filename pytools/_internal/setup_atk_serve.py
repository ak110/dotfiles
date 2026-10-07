"""`atk serve`のLinux向け自動起動セットアップ。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
特定ホストでのみsystemd user serviceユニットをべき等に配置・有効化する。
"""

import json
import logging
import pathlib
import stat

from pytools._internal import claude_common, log_format, post_apply_outcome, systemd_user_unit

logger = logging.getLogger(__name__)

_SERVICE_UNIT = "atk-serve.service"
# 計画ファイル閲覧を統合する前に配置していたサービス。移行時に停止・無効化して除去する。
_LEGACY_SERVICE_UNIT = "claude-plans-viewer.service"
_LAUNCHER_RELATIVE = pathlib.PurePath(".local") / "bin" / "atk-serve"
_UNIT_PATH_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user" / _SERVICE_UNIT
_LEGACY_UNIT_PATH_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user" / _LEGACY_SERVICE_UNIT

# ランチャー本文のテンプレート。dotfiles 作業ツリーの agent-toolkit を直接参照し、
# Claude Code のプラグインキャッシュ配置に依存せず解決先を1点に定める。
# uv は systemd user service の PATH に存在しないため、導入時に解決した絶対パスを埋め込む。
# ~/.local/bin/atk は install-claude.sh がagent-toolkit単体のユーザー向けに生成するラッパーで
# 内容が競合するため、本モジュールはサービス専用の別名を用いる。
# dotfiles ホストでは post_apply の旧配布物削除が ~/.local/bin/atk を除去する。
_LAUNCHER_TEMPLATE = """#!/bin/sh
set -eu
exec "{uv}" run --project "{dotfiles}/agent-toolkit" --locked --no-default-groups \\
  "{dotfiles}/agent-toolkit/agent_toolkit/atk.py" serve "$@"
"""

# unit ファイル本文。ExecStart は systemd specifier %h を使い、
# post_apply 実行時の Path.home() を埋め込まない。
# 待受アドレス・ポートはホスト固有値であり、
# `~/.config/agent-toolkit/serve.toml` 経由で指定する。
_UNIT_CONTENT = f"""[Unit]
Description=agent-toolkit work item server
After=network.target

[Service]
Type=simple
{systemd_user_unit.USER_UNIT_PATH_ENVIRONMENT}ExecStart=%h/.local/bin/atk-serve
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=10

[Install]
WantedBy=default.target
"""


def run() -> post_apply_outcome.PostApplyOutcome:
    """`atk serve`の systemd 自動起動状態を整える (euryale のみ)。

    対象ホストで状態を確認した場合は変更あり、ホスト不一致や uv・dotfiles ルートを解決できず何もしなかった場合は変更なしを返す。
    旧unitの撤去の失敗は失敗と数え、ランチャーとunitの配置は続ける。

    Raises:
        systemd_user_unit.SetupError: restart 後にサービスが常駐状態へ至らない場合に送出する。
        OSError: ランチャーを書き込めない場合に送出する。
    """
    if not claude_common.is_euryale():
        return post_apply_outcome.PostApplyOutcome()

    uv = claude_common.resolve_uv_path()
    if uv is None:
        logger.info(log_format.format_status("atk-serve", "uvが見つからないため設定を見送る"))
        return post_apply_outcome.PostApplyOutcome()
    dotfiles = claude_common.find_dotfiles_root()
    if dotfiles is None:
        logger.info(log_format.format_status("atk-serve", "dotfilesルートが見つからないため設定を見送る"))
        return post_apply_outcome.PostApplyOutcome()

    legacy_failure = _remove_legacy_unit()

    launcher = _launcher_path()
    content = _LAUNCHER_TEMPLATE.format(uv=uv, dotfiles=dotfiles)
    launcher_changed = _read_text(launcher) != content
    if launcher_changed and not claude_common.atomic_write_text(launcher, content, mode=0o755, tag="atk-serve"):
        raise OSError(f"{launcher} の書き込みに失敗")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    plugin_manifest = dotfiles / "agent-toolkit" / ".claude-plugin" / "plugin.json"
    plugin_version = json.loads(plugin_manifest.read_text(encoding="utf-8"))["version"]
    # 配布版の変更をunit本文へ反映し、稼働中プロセスも新しいplugin実装へ切り替える。
    unit_content = f"{_UNIT_CONTENT}# agent-toolkit version: {plugin_version}\n"
    changed = systemd_user_unit.setup(
        unit_path=_unit_path(),
        executable_path=launcher,
        unit_content=unit_content,
        log_tag="atk-serve",
        service_name=_SERVICE_UNIT,
        restart_needed=launcher_changed,
        journal_identifier="atk-serve-setup",
    )
    return post_apply_outcome.PostApplyOutcome(changed=changed, failure=legacy_failure)


def _remove_legacy_unit() -> str | None:
    """旧計画ビューアーのsystemd unitを停止・無効化してから削除し、失敗した場合はその内容を返す。

    停止、無効化または削除に失敗した場合はunitファイルを残し、後続の配置を続ける。
    失敗は撤去の失敗として工程の失敗に数えるが、主目的であるサービス設定の配置は止めない。
    削除より先にunitファイルを除去すると、稼働中のサービスを停止も無効化もできないまま残すため、
    `post_apply`の一括削除ではなく本工程で扱う。
    """
    unit_path = _legacy_unit_path()
    if not unit_path.exists():
        return None
    result = claude_common.run_subprocess(
        ["systemctl", "--user", "disable", "--now", _LEGACY_SERVICE_UNIT],
        timeout=30.0,
        tag="atk-serve",
    )
    if result is None or result.returncode != 0:
        return f"{_LEGACY_SERVICE_UNIT}を停止できないためunitを残した: {claude_common.format_cli_error(result)}"
    try:
        unit_path.unlink(missing_ok=True)
    except OSError as error:
        return f"{_LEGACY_SERVICE_UNIT}のunitファイルを削除できないため残した: {error}"
    claude_common.run_subprocess(["systemctl", "--user", "daemon-reload"], timeout=30.0, tag="atk-serve")
    logger.info(log_format.format_status("atk-serve", f"{_LEGACY_SERVICE_UNIT}を停止して削除した"))
    return None


def _read_text(path: pathlib.Path) -> str | None:
    """ファイル内容を返す。存在しない場合は None を返す。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _unit_path() -> pathlib.Path:
    """Unit ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _UNIT_PATH_RELATIVE


def _legacy_unit_path() -> pathlib.Path:
    """旧計画ビューアーのUnitファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _LEGACY_UNIT_PATH_RELATIVE


def _launcher_path() -> pathlib.Path:
    """サービス専用ランチャーの絶対パスを返す。"""
    return pathlib.Path.home() / _LAUNCHER_RELATIVE
