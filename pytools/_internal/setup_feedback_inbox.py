"""特定ホストでフィードバック蓄積機能を有効化するためのフラグファイルを生成する。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
対象ホストでのみ`~/.config/agent-toolkit/feedback-inbox.enabled`を配置する。
対象外ホストでは同ファイルが存在する場合に削除する。
"""

import logging
import pathlib
import socket

from pytools._internal import log_format

logger = logging.getLogger(__name__)

_TARGET_HOSTS: tuple[str, ...] = ("stheno", "circe", "circe-container", "euryale", "euryale-container")
_FLAG_PATH = pathlib.Path.home() / ".config" / "agent-toolkit" / "feedback-inbox.enabled"


def run() -> bool:
    """対象ホストでフラグファイルを配置し、非対象ホストでは削除する。

    Returns:
        フラグファイルの生成または削除を実施した場合True、何もしなかった場合False。
    """
    hostname = socket.gethostname().lower().split(".")[0]
    if hostname in _TARGET_HOSTS:
        return _ensure_present()
    return _ensure_absent()


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


def _ensure_absent() -> bool:
    """対象外ホストでフラグファイルを削除する。

    Returns:
        削除した場合True、存在しないため何もしなかった場合False。
    """
    if not _FLAG_PATH.exists():
        return False
    try:
        _FLAG_PATH.unlink()
        logger.info(log_format.format_status("feedback-inbox", f"対象外ホストのため削除: {_FLAG_PATH}"))
        return True
    except OSError as e:
        logger.warning(log_format.format_status("feedback-inbox", f"削除失敗: {e}"))
        return False
