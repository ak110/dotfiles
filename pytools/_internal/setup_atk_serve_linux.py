"""`atk serve`のLinux向け自動起動セットアップ。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
特定ホストでのみsystemd user serviceユニットをべき等に配置・有効化する。
"""

import logging
import pathlib
import shutil
import socket
import stat
import sys

from pytools._internal import claude_common, log_format, systemd_user_unit

assert claude_common  # 既存テストと外部monkeypatch契約を共通モジュール移行後も維持する。

logger = logging.getLogger(__name__)

_TARGET_HOSTNAME = "euryale"
_SERVICE_UNIT = "atk-serve.service"
_LAUNCHER_RELATIVE = pathlib.PurePath(".local") / "bin" / "atk-serve"
_UNIT_PATH_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user" / _SERVICE_UNIT
_UV_RELATIVE = pathlib.PurePath(".local") / "bin" / "uv"

# ランチャー本文のテンプレート。agent-toolkit プラグインはバージョン付きディレクトリへ
# 展開されるため、最新バージョンの scripts/atk.py を起動時に解決する。
# uv は systemd user service の PATH に存在しないため、導入時に解決した絶対パスを埋め込む。
# ~/.local/bin/atk は install-claude.sh がプラグイン単体利用者向けに生成するラッパーで
# 内容が競合するため、本モジュールはサービス専用の別名を用いる。
_LAUNCHER_TEMPLATE = """#!/bin/sh
set -eu
script=$(find "$HOME/.claude/plugins/cache" -path '*/agent-toolkit/*/scripts/atk.py' \\
  -type f 2>/dev/null | sort -V | tail -1)
exec "{uv}" run --no-project --script "$script" serve "$@"
"""

# unit ファイル本文。ExecStart は systemd specifier %h を使い、
# post_apply 実行時の Path.home() を埋め込まない。
# 待受アドレス・ポートはホスト固有値であり、
# `~/.config/agent-toolkit/serve.toml` 経由で指定する。
_UNIT_CONTENT = """[Unit]
Description=agent-toolkit feedback server
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/atk-serve
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStopSec=10

[Install]
WantedBy=default.target
"""


def run() -> bool:
    """`atk serve`の systemd 自動起動セットアップと restart を行う (Linux + euryale のみ)。

    Returns:
        セットアップまたは restart を実施した場合 True、ホスト不一致・非 Linux・
        uv 未検出で何もしなかった場合 False。

    Raises:
        systemd_user_unit.SetupError: restart 後にサービスが常駐状態へ至らない場合に送出する。
    """
    if sys.platform != "linux":
        return False

    hostname = socket.gethostname().lower().split(".")[0]
    if hostname != _TARGET_HOSTNAME:
        return False

    uv = _uv_path()
    if uv is None:
        logger.info(log_format.format_status("atk-serve", "uvが見つからないため設定を見送る"))
        return False

    launcher = _launcher_path()
    content = _LAUNCHER_TEMPLATE.format(uv=uv)
    if _read_text(launcher) != content:
        claude_common.atomic_write_text(launcher, content, mode=0o755, tag="atk-serve")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return systemd_user_unit.setup(
        unit_path=_unit_path(),
        executable_path=launcher,
        unit_content=_UNIT_CONTENT,
        log_tag="atk-serve",
        service_name=_SERVICE_UNIT,
    )


def _uv_path() -> pathlib.Path | None:
    """実行ファイル uv の絶対パスを返す。存在しない場合は None を返す。

    公式インストーラーの導入先 `~/.local/bin/uv` を優先する。
    mise の shim は systemd user service の環境でバージョン未解決となり起動しないため、
    PATH 探索は同ディレクトリーに uv が無い場合のフォールバックに留める。
    誤った候補を採用した場合は systemd_user_unit の常駐確認が導入時に検知する。
    """
    candidate = pathlib.Path.home() / _UV_RELATIVE
    if candidate.is_file():
        return candidate
    found = shutil.which("uv")
    return pathlib.Path(found) if found else None


def _read_text(path: pathlib.Path) -> str | None:
    """ファイル内容を返す。存在しない場合は None を返す。"""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _unit_path() -> pathlib.Path:
    """Unit ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _UNIT_PATH_RELATIVE


def _launcher_path() -> pathlib.Path:
    """サービス専用ランチャーの絶対パスを返す。"""
    return pathlib.Path.home() / _LAUNCHER_RELATIVE
