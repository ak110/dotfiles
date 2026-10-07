"""agents_serverとbackendが送出する、委譲先sessionの起動と所有に関する例外。"""

from __future__ import annotations

import logging

from agent_toolkit._common.next_action import ActionableError


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
