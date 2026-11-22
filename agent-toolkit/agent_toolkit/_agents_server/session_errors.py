from __future__ import annotations

import logging

from agent_toolkit._common.next_action import ActionableError

KILL_NOT_DELIVERED_NEXT_ACTION = (
    "`atk agents wait`で状態を確認し、turnが続いていて中断が必要なら`kill`を再発行する。"
    "再発行も配送を確定できなければ同じ前提で繰り返さず、`show`で所有サーバーと活動を確かめる。"
    "`active_tool_uses`が同じ開始時刻のまま続く間と`api_error`が続く間は待機し、"
    "どちらも無く活動が止まっている時だけ停滞として巻き取る"
)


class ProcessIdentityMismatch(PermissionError):
    pass


class SessionInitializationTimeoutError(RuntimeError):
    pass


class DelegateBackendError(RuntimeError):
    """委譲先CLIの起動、または委譲先CLIとの通信の形式が想定と異なることを示す。

    MCPのツール処理の共通層は本例外を受け取ると、CLIの導入と認証の確認と別engineでの再起動を次の操作として返す。
    """


class ActionableRuntimeError(ActionableError, RuntimeError):
    pass


class ActionableTimeoutError(ActionableError, TimeoutError):
    pass


class SessionOwnerGoneError(RuntimeError):
    pass


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
