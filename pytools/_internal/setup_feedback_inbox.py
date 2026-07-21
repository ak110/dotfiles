"""特定ホストでフィードバック蓄積機能を有効化するためのフラグファイルを生成する。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
対象ホストでのみ`~/.config/agent-toolkit/feedback-inbox.enabled`を配置する。
対象外ホストでは何もしない（手動で有効化された設定を尊重する）。
"""

import pathlib
import socket

from pytools._internal import claude_common

_FLAG_PATH = pathlib.Path.home() / ".config" / "agent-toolkit" / "feedback-inbox.enabled"


def run() -> bool:
    """対象ホストでフラグファイルを配置する。対象外ホストでは何もしない。

    Returns:
        フラグファイルを新規生成した場合True、既存または対象外ホストの場合False。
    """
    if not claude_common.is_target_host(socket.gethostname()):
        return False
    return claude_common.ensure_flag_file_present(_FLAG_PATH, tag="feedback-inbox")
