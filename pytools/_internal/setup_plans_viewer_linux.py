"""claude-plans-viewerのLinux向け自動起動セットアップ。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
特定ホストでのみsystemd user serviceユニットをべき等に配置・有効化する。
"""

import logging
import pathlib

from pytools._internal import claude_common, log_format, systemd_user_unit

assert claude_common  # 既存テストと外部monkeypatch契約を共通モジュール移行後も維持する。

logger = logging.getLogger(__name__)

_SERVICE_UNIT = "claude-plans-viewer.service"
_VIEWER_EXE_RELATIVE = pathlib.PurePath(".local") / "bin" / "claude-plans-viewer"
_UNIT_PATH_RELATIVE = pathlib.PurePath(".config") / "systemd" / "user" / _SERVICE_UNIT

# unit ファイル本文。ExecStart は systemd specifier %h を使い、
# post_apply 実行時の Path.home() を埋め込まない。
# 待受アドレス・リモートホストはホスト固有値であり、
# `~/.config/pytools/claude-plans-viewer.toml` 経由で指定する。
# 当該設定ファイルは chezmoi が euryale 限定で配布するため、
# 本モジュールが対応するホストと配布対象ホストが一致する。
_UNIT_CONTENT = """\
[Unit]
Description=Claude plans viewer
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/claude-plans-viewer
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStopSec=10

[Install]
WantedBy=default.target
"""


def run() -> bool:
    """Viewer の systemd 自動起動セットアップと restart を行う (Linux + euryale のみ)。

    Returns:
        セットアップまたは restart を 1 つでも実施した場合 True、ホスト不一致や非 Linux で
        何もしなかった場合 False。

    Raises:
        systemd_user_unit.SetupError: restart 後にサービスが常駐状態へ至らない場合に送出する。
    """
    if not claude_common.is_euryale():
        return False

    exe = _viewer_exe()
    if not exe.is_file():
        logger.info(log_format.format_status("plans-viewer", f"実行ファイルが未配置: {exe}"))
        return False

    return systemd_user_unit.setup(
        unit_path=_unit_path(),
        executable_path=exe,
        unit_content=_UNIT_CONTENT,
        log_tag="plans-viewer",
        service_name=_SERVICE_UNIT,
    )


def _unit_path() -> pathlib.Path:
    """Unit ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _UNIT_PATH_RELATIVE


def _viewer_exe() -> pathlib.Path:
    """Viewer 実行ファイルの絶対パスを返す。"""
    return pathlib.Path.home() / _VIEWER_EXE_RELATIVE
