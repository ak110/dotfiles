"""claude-plans-viewer の Linux 向け自動起動セットアップ。

本モジュールは `chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
ホスト名が `euryale` のときに限り、systemd user service ユニット
(`~/.config/systemd/user/claude-plans-viewer.service`) を冪等に配置・有効化し、
viewer を常駐起動させる。

ホスト判定は euryale 1台のみとし、その他の Linux ホストでは no-op (False 返却) として、
`setup_plans_viewer_windows.run` と同じ `sys.platform` 分岐形式にそろえる。
linger 状態が無効の場合はエンドユーザー向けに 1 行のログで手動有効化を案内する
（`loginctl enable-linger` を post_apply から sudo で発火しない設計）。
"""

import getpass
import logging
import pathlib
import socket
import sys

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_TARGET_HOSTNAME = "euryale"
_VIEWER_EXE_RELATIVE = pathlib.PurePath(".local") / "bin" / "claude-plans-viewer"
_UNIT_PATH_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user" / "claude-plans-viewer.service"

# unit ファイル本文。ExecStart は systemd specifier %h を使い、
# post_apply 実行時の Path.home() を埋め込まない。
_UNIT_CONTENT = """\
[Unit]
Description=Claude plans viewer
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/claude-plans-viewer --host=192.168.111.128 --remote-host=circe --remote-host=stheno
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def run() -> bool:
    """Viewer の systemd 自動起動セットアップを行う (Linux + euryale のみ)。

    Returns:
        unit ファイルの書き込みまたは systemctl 操作をいずれか 1 つでも実施した場合 True。
    """
    if sys.platform != "linux":
        return False

    hostname = socket.gethostname().lower().split(".")[0]
    if hostname != _TARGET_HOSTNAME:
        return False

    exe = _viewer_exe()
    if not exe.is_file():
        logger.info(log_format.format_status("plans-viewer", f"実行ファイルが未配置: {exe}"))
        return False

    try:
        unit_changed = _ensure_unit_file()
        _apply_systemctl_state(unit_changed)
        _warn_if_linger_disabled()
        return unit_changed
    except Exception as e:  # noqa: BLE001
        logger.info(log_format.format_status("plans-viewer", f"自動起動セットアップに失敗: {e}"))
        return False


def _unit_path() -> pathlib.Path:
    """Unit ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _UNIT_PATH_RELATIVE


def _viewer_exe() -> pathlib.Path:
    """Viewer 実行ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _VIEWER_EXE_RELATIVE


def _ensure_unit_file() -> bool:
    """Unit ファイルを冪等に書き込む。

    Returns:
        ファイルを新規書き込みした場合 True、既存内容と一致するため書き込み不要の場合 False。
    """
    path = _unit_path()
    try:
        existing = path.read_bytes()
        if existing == _UNIT_CONTENT.encode("utf-8"):
            return False
    except FileNotFoundError:
        pass

    claude_common.atomic_write_text(path, _UNIT_CONTENT, mode=0o644, tag="plans-viewer")
    logger.info(log_format.format_status("plans-viewer", f"ユニット配置: {path}"))
    return True


def _apply_systemctl_state(unit_changed: bool) -> None:
    """Systemd の状態を冪等に更新する。

    unit_changed が True なら daemon-reload を実行し、
    is-enabled / is-active の状態に応じて enable --now / start / restart を発火する。
    """
    if unit_changed:
        logger.info(log_format.format_status("plans-viewer", "ユニット daemon-reload を実行"))
        result = claude_common.run_subprocess(["systemctl", "--user", "daemon-reload"], timeout=15.0, tag="plans-viewer")
        if result is None or result.returncode != 0:
            rc = result.returncode if result is not None else "N/A"
            logger.warning(log_format.format_status("plans-viewer", f"daemon-reload: 失敗 (exit {rc})"))

    enable_now_fired = False
    start_fired = False

    result = claude_common.run_subprocess(
        ["systemctl", "--user", "is-enabled", "claude-plans-viewer.service"],
        timeout=15.0,
        tag="plans-viewer",
    )
    is_enabled_status = result.stdout.strip() if result is not None else ""

    if is_enabled_status != "enabled":
        logger.info(log_format.format_status("plans-viewer", "サービスを enable --now"))
        result = claude_common.run_subprocess(
            ["systemctl", "--user", "enable", "--now", "claude-plans-viewer.service"],
            timeout=15.0,
            tag="plans-viewer",
        )
        if result is None or result.returncode != 0:
            rc = result.returncode if result is not None else "N/A"
            logger.warning(log_format.format_status("plans-viewer", f"enable --now: 失敗 (exit {rc})"))
        enable_now_fired = True
    else:
        result = claude_common.run_subprocess(
            ["systemctl", "--user", "is-active", "--quiet", "claude-plans-viewer.service"],
            timeout=15.0,
            tag="plans-viewer",
        )
        if result is not None and result.returncode != 0:
            logger.info(log_format.format_status("plans-viewer", "サービスを start"))
            result = claude_common.run_subprocess(
                ["systemctl", "--user", "start", "claude-plans-viewer.service"],
                timeout=15.0,
                tag="plans-viewer",
            )
            if result is None or result.returncode != 0:
                rc = result.returncode if result is not None else "N/A"
                logger.warning(log_format.format_status("plans-viewer", f"start: 失敗 (exit {rc})"))
            start_fired = True

    if unit_changed and not enable_now_fired and not start_fired:
        logger.info(log_format.format_status("plans-viewer", "サービスを restart"))
        result = claude_common.run_subprocess(
            ["systemctl", "--user", "restart", "claude-plans-viewer.service"],
            timeout=15.0,
            tag="plans-viewer",
        )
        if result is None or result.returncode != 0:
            rc = result.returncode if result is not None else "N/A"
            logger.warning(log_format.format_status("plans-viewer", f"restart: 失敗 (exit {rc})"))


def _warn_if_linger_disabled() -> None:
    """Linger 状態を確認し、無効なら手動有効化を案内する 1 行ログを出力する。"""
    user = getpass.getuser()
    result = claude_common.run_subprocess(
        ["loginctl", "show-user", user, "--property=Linger"],
        timeout=15.0,
        tag="plans-viewer",
    )
    if result is None or result.returncode != 0:
        # loginctl が無い環境（コンテナ等）ではスキップ
        return
    if "Linger=no" in result.stdout:
        logger.info(
            log_format.format_status(
                "plans-viewer",
                f"linger 無効: ログアウト中も常駐させるには `sudo loginctl enable-linger {user}` を手動実行する",
            )
        )
