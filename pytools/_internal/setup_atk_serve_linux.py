"""Linuxの特定ホストへ`atk serve`自動起動を設定する。"""

import logging
import os
import pathlib
import socket
import stat
import sys

from pytools._internal import claude_common, log_format, systemd_user_unit

logger = logging.getLogger(__name__)
_TARGET_HOSTNAME = "euryale"
_SERVICE_NAME = "atk-serve.service"
_LAUNCHER = """#!/bin/sh
set -eu
plugin_root=$(find "$HOME/.claude/plugins/cache" -path '*/agent-toolkit/*/bin/atk' \
  -type f -printf '%h/..\\n' 2>/dev/null | sort -V | tail -1)
exec "$plugin_root/bin/atk" "$@"
"""
_UNIT_CONTENT = """[Unit]
Description=agent-toolkit feedback server
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/atk serve
Restart=on-failure
RestartSec=5
KillMode=mixed
TimeoutStopSec=10

[Install]
WantedBy=default.target
"""


def run() -> bool:
    """条件一致時にランチャーとsystemd user unitを設定する。"""
    if sys.platform != "linux" or socket.gethostname().lower().split(".")[0] != _TARGET_HOSTNAME:
        return False
    launcher = pathlib.Path.home() / ".local/bin/atk"
    unit_path = pathlib.Path.home() / ".config/systemd/user" / _SERVICE_NAME
    try:
        try:
            current = launcher.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = None
        if current != _LAUNCHER:
            claude_common.atomic_write_text(launcher, _LAUNCHER, mode=0o755, tag="atk-serve")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if not os.access(launcher, os.X_OK):
            logger.info(log_format.format_status("atk-serve", f"ランチャーを実行可能にできません: {launcher}"))
            return False
        return systemd_user_unit.setup(
            unit_path=unit_path,
            executable_path=launcher,
            unit_content=_UNIT_CONTENT,
            log_tag="atk-serve",
            service_name=_SERVICE_NAME,
        )
    except Exception as error:  # noqa: BLE001
        logger.info(log_format.format_status("atk-serve", f"自動起動セットアップに失敗: {error}"))
        return False
