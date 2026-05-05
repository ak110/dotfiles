"""claude-plans-viewerのLinux向け自動起動セットアップ。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
特定ホストでのみsystemd user serviceユニットをべき等に配置・有効化する。
"""

import getpass
import logging
import pathlib
import socket
import sys

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_TARGET_HOSTNAME = "euryale"
_SERVICE_UNIT = "claude-plans-viewer.service"
_VIEWER_EXE_RELATIVE = pathlib.PurePath(".local") / "bin" / "claude-plans-viewer"
_UNIT_PATH_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user" / _SERVICE_UNIT

# unit ファイル本文。ExecStart は systemd specifier %h を使い、
# post_apply 実行時の Path.home() を埋め込まない。
# bind は localhost に固定し、外部公開は逆プロキシ等の前段へ委ねる。
_UNIT_CONTENT = """\
[Unit]
Description=Claude plans viewer
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/claude-plans-viewer --host=localhost --remote-host=circe --remote-host=stheno
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def run() -> bool:
    """Viewer の systemd 自動起動セットアップと restart を行う (Linux + euryale のみ)。

    Returns:
        セットアップまたは restart を 1 つでも実施した場合 True、ホスト不一致や非 Linux で
        何もしなかった場合 False。
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
        return True
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

    unit が変化した場合は daemon-reload を実行する。enable は systemctl 側で冪等のため毎回呼ぶ。
    最後に viewer のコード更新を反映するため必ず restart する。
    """
    if unit_changed:
        logger.info(log_format.format_status("plans-viewer", "ユニット daemon-reload を実行"))
        result = claude_common.run_subprocess(
            ["systemctl", "--user", "daemon-reload"],
            timeout=15.0,
            tag="plans-viewer",
        )
        if result is None or result.returncode != 0:
            rc = result.returncode if result is not None else "N/A"
            logger.warning(log_format.format_status("plans-viewer", f"daemon-reload: 失敗 (exit {rc})"))

    logger.info(log_format.format_status("plans-viewer", "サービスを enable"))
    result = claude_common.run_subprocess(
        ["systemctl", "--user", "enable", _SERVICE_UNIT],
        timeout=15.0,
        tag="plans-viewer",
    )
    if result is None or result.returncode != 0:
        rc = result.returncode if result is not None else "N/A"
        logger.warning(log_format.format_status("plans-viewer", f"enable: 失敗 (exit {rc})"))

    logger.info(log_format.format_status("plans-viewer", f"サービスを restart ({_SERVICE_UNIT})"))
    result = claude_common.run_subprocess(
        ["systemctl", "--user", "restart", _SERVICE_UNIT],
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
        # loginctl が無い環境 (コンテナ等) ではスキップ
        return
    if "Linger=no" in result.stdout:
        logger.info(
            log_format.format_status(
                "plans-viewer",
                f"linger 無効: ログアウト中も常駐させるには `sudo loginctl enable-linger {user}` を手動実行する",
            )
        )
