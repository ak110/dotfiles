"""特定ホストでレビューのバランスモードを「claude寄り」へ設定するフラグファイルを生成する。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、
対象ホストでのみ`~/.config/agent-toolkit/review-balance-mode.claude-heavy`を配置する。
対象外ホストでは何もしない（既定は未設定＝「codex寄り」）。
"""

import pathlib
import socket

from pytools._internal import claude_common

_FLAG_PATH = pathlib.Path.home() / ".config" / "agent-toolkit" / "review-balance-mode.claude-heavy"


def run() -> bool:
    """対象ホストでフラグファイルを配置する。対象外ホストでは何もしない。

    Returns:
        フラグファイルを新規生成した場合True、既存または対象外ホストの場合False。
    """
    if not claude_common.is_target_host(socket.gethostname()):
        return False
    return claude_common.ensure_flag_file_present(_FLAG_PATH, tag="review-balance-mode")
