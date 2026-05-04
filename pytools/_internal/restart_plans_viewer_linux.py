"""claude-plans-viewer の Linux 向け再起動ステップ。

`chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれ、特定ホスト（euryale）でのみ
`systemctl --user restart claude-plans-viewer.service` を発火する。
unit ファイル本体はリポジトリ管理外とし、euryale 上で別途維持されている前提。

ホスト判定は euryale 1台のみ。その他の Linux ホストでは no-op (False 返却)。
sys.platform 分岐の形式を `setup_*.py` 系の他ステップと揃える。
"""

import logging
import socket
import sys

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_TARGET_HOSTNAME = "euryale"
_SERVICE_UNIT = "claude-plans-viewer.service"


def run() -> bool:
    """Viewer サービスを再起動する (Linux + euryale のみ)。

    Returns:
        systemctl restart を発火した場合 True、ホスト不一致や非 Linux で何もしなかった場合 False。
    """
    if sys.platform != "linux":
        return False

    hostname = socket.gethostname().lower().split(".")[0]
    if hostname != _TARGET_HOSTNAME:
        return False

    logger.info(log_format.format_status("plans-viewer", f"サービスを restart ({_SERVICE_UNIT})"))
    result = claude_common.run_subprocess(
        ["systemctl", "--user", "restart", _SERVICE_UNIT],
        timeout=15.0,
        tag="plans-viewer",
    )
    if result is None or result.returncode != 0:
        rc = result.returncode if result is not None else "N/A"
        logger.warning(log_format.format_status("plans-viewer", f"restart: 失敗 (exit {rc})"))
        return False
    return True
