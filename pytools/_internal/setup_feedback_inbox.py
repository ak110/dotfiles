"""特定ホストでフィードバック蓄積機能を有効化するためのフラグファイルを生成する。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
対象ホストでのみ`~/.config/agent-toolkit/feedback-inbox.enabled`を配置する。
対象外ホストでは何もしない（手動で有効化された設定を尊重する）。
"""

import logging
import pathlib
import socket

from pytools._internal import log_format

logger = logging.getLogger(__name__)

_TARGET_HOSTS: tuple[str, ...] = ("stheno", "circe", "circe-container", "euryale", "euryale-container")
_FLAG_PATH = pathlib.Path.home() / ".config" / "agent-toolkit" / "feedback-inbox.enabled"


def run() -> bool:
    """対象ホストでフラグファイルを配置する。対象外ホストでは何もしない。

    Returns:
        フラグファイルを新規生成した場合True、既存または対象外ホストの場合False。
    """
    hostname = socket.gethostname().lower().split(".")[0]
    if hostname not in _TARGET_HOSTS:
        return False
    return _ensure_present()


def _ensure_present() -> bool:
    """フラグファイルを冪等に生成する。

    Returns:
        新規生成した場合True、既存のため生成不要の場合False。
    """
    if _FLAG_PATH.exists():
        return False
    _FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FLAG_PATH.write_bytes(b"")
    logger.info(log_format.format_status("feedback-inbox", f"フラグファイルを生成: {_FLAG_PATH}"))
    return True
