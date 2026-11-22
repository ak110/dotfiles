"""agents_serverとbackendが送出する、管理サービスの通信と委譲先sessionの起動・所有に関する例外。"""

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
    """同じアカウントの相手が発見記録の起動単位と異なる。"""


class SessionInitializationTimeoutError(RuntimeError):
    """backendがsessionの初期化を上限内に完了せず、起動を打ち切ったことを示す。

    起動を要求した主体は無制限に待たされる代わりに本例外を受領し、対象と原因を報告できる。
    """


class DelegateBackendError(RuntimeError):
    """委譲先CLIの起動、または委譲先CLIとの通信の形式が想定と異なることを示す。

    MCPのツール処理の共通層は本例外を受け取ると、CLIの導入と認証の確認と別engineでの再起動を次の操作として返す。
    """


class ActionableRuntimeError(ActionableError, RuntimeError):
    """次の操作を持つ`RuntimeError`。既存の`except RuntimeError`節が捕捉する範囲を保つために使う。"""


class ActionableTimeoutError(ActionableError, TimeoutError):
    """次の操作を持つ`TimeoutError`。既存の`except TimeoutError`節が捕捉する範囲を保つために使う。"""


class SessionOwnerGoneError(RuntimeError):
    """session所有タスクが終了し、継続要求または中断要求を配送できないことを示す。

    MCP層はこの例外を受領して、保存済みの再開状態から同じ会話を再開する。
    """


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
