"""stdio上のMCPの初期化の節目を診断ログへ残し、ツールの例外を次の操作付きのエラー本文へ変えるFastMCPの拡張。"""

from __future__ import annotations

import functools
import inspect
import logging
import typing
from collections.abc import Callable
from typing import Any, cast

from anyio.abc import ObjectReceiveStream, ObjectSendStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import Icon, ToolAnnotations

from agent_toolkit._agents_server.session_errors import DelegateBackendError, SessionInitializationTimeoutError
from agent_toolkit._agents_server.tool_descriptions import KIND_MCP_TOOL, schema_text
from agent_toolkit._common.next_action import ActionableError, with_next_action

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


class _InitializationLogTracker:
    """initialize要求と対応応答の節目だけを永続ログへ記録する。"""

    def __init__(self) -> None:
        self._pending_request_ids: set[str | int] = set()

    def receive(self, message: SessionMessage | Exception) -> None:
        root = getattr(getattr(message, "message", None), "root", None)
        if getattr(root, "method", None) != "initialize":
            return
        request_id = getattr(root, "id", None)
        if not isinstance(request_id, (str, int)):
            return
        self._pending_request_ids.add(request_id)
        _LOG.info("FastMCP initializeを受信しました: request_id=%s", request_id)

    def sent(self, message: SessionMessage) -> None:
        root = getattr(message.message, "root", None)
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        error = getattr(root, "error", None)
        if error is not None:
            _LOG.error(
                "FastMCP initialize応答が失敗しました: request_id=%s exception_type=%s exception=%s",
                request_id,
                type(error).__name__,
                getattr(error, "message", error),
            )
            return
        _LOG.info("FastMCP initialize応答が完了しました: request_id=%s", request_id)

    def send_failed(self, message: SessionMessage, exc: BaseException) -> None:
        root = getattr(message.message, "root", None)
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        _LOG.error(
            "FastMCP initialize応答の送信に失敗しました: request_id=%s exception_type=%s exception=%s",
            request_id,
            type(exc).__name__,
            exc,
        )

    def transport_closed(self) -> None:
        for request_id in sorted(self._pending_request_ids, key=str):
            _LOG.error(
                "FastMCP initializeが未完了のままtransportが終了しました: "
                "request_id=%s exception_type=RuntimeError exception=transport closed before initialize response",
                request_id,
            )
        self._pending_request_ids.clear()


class _InitializationLoggingReceiveStream(ObjectReceiveStream[SessionMessage | Exception]):
    def __init__(
        self,
        stream: ObjectReceiveStream[SessionMessage | Exception],
        tracker: _InitializationLogTracker,
    ) -> None:
        self._stream = stream
        self._tracker = tracker

    async def receive(self) -> SessionMessage | Exception:
        message = await self._stream.receive()
        self._tracker.receive(message)
        return message

    async def aclose(self) -> None:
        await self._stream.aclose()


class _InitializationLoggingSendStream(ObjectSendStream[SessionMessage]):
    def __init__(self, stream: ObjectSendStream[SessionMessage], tracker: _InitializationLogTracker) -> None:
        self._stream = stream
        self._tracker = tracker

    async def send(self, item: SessionMessage) -> None:
        try:
            await self._stream.send(item)
        except BaseException as exc:
            self._tracker.send_failed(item, exc)
            raise
        self._tracker.sent(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


def _unexpected_error_next_action(error: Exception) -> str:
    """共通の例外型でない例外へ、失敗の種類に応じた次の操作を返す。"""
    if isinstance(error, SessionInitializationTimeoutError):
        return (
            "ホストのCLI（`claude`・`codex`・`agy`）の認証状態を確かめ、`model_type`へ別の候補を指定して起動し直す。"
            "原因はエラー本文のdiagnosticと、session_idが分かる場合は`atk agents logs <session_id>`で調べる"
        )
    # Claude Agent SDKの例外（CLIの未導入、接続失敗、プロセス異常）はbackendが包まずに届くため、型の所属で同じ分類にする。
    if isinstance(error, DelegateBackendError) or type(error).__module__.startswith("claude_agent_sdk"):
        return "委譲先CLIの導入と認証を確かめ、`model_type`へ別のengineの候補を指定して起動し直す"
    if isinstance(error, ValueError):
        return (
            "ツールの説明で引数の受理形式を確かめて再発行する。"
            "解消しない場合はエラー本文を添えてagents_serverの不具合としてユーザーへ報告する"
        )
    return (
        "`list`か`show`で対象sessionの状態を確かめてから再発行する。"
        "再発する場合はエラー本文を添えてagents_serverの不具合としてユーザーへ報告する"
    )


def _actionable_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """ツール関数の例外を、理由と次の操作の2行を本文とするツールのエラーへ変える。

    FastMCPは例外の`str()`をエラー本文へ使う。共通の例外型の`str()`は理由だけを返すため、
    ここで次の操作の行を加えないと委譲元へ届かない。入力スキーマはFastMCPが`__wrapped__`の署名から生成するため変わらない。
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except ActionableError as error:
            raise ToolError(error.message) from error
        except Exception as error:
            reason = str(error) or type(error).__name__
            raise ToolError(with_next_action(reason, _unexpected_error_next_action(error))) from error
        return result

    return wrapper


class AgentsServerFastMCP(FastMCP[Any]):
    """stdio上のinitialize節目を診断ログへ残し、ツールの説明文へ境界と例外の次の操作を付けるFastMCP。"""

    @typing.override
    def add_tool(  # noqa: PLR0913 -- 上位の署名をそのまま受け取る
        self,
        fn: Callable[..., Any],
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        """ツールの説明文へ境界を付け、例外を次の操作付きのエラー本文へ変えて登録する。

        説明文は実行ホストがスキーマとしてsystem promptへ載せる。登録の1箇所で囲むことで、
        ツールごとの書き分けを増やさずに全てのツールへ同じ境界と次の操作を付ける。
        """
        resolved = description if description is not None else inspect.getdoc(fn) or ""
        super().add_tool(
            _actionable_tool(fn),
            name,
            title,
            schema_text(resolved, kind=KIND_MCP_TOOL),
            annotations,
            icons,
            meta,
            structured_output,
        )

    async def run_stdio_async(self) -> None:
        tracker = _InitializationLogTracker()
        async with stdio_server() as (read_stream, write_stream):
            try:
                await self._mcp_server.run(
                    cast(
                        MemoryObjectReceiveStream[SessionMessage | Exception],
                        _InitializationLoggingReceiveStream(read_stream, tracker),
                    ),
                    cast(
                        MemoryObjectSendStream[SessionMessage],
                        _InitializationLoggingSendStream(write_stream, tracker),
                    ),
                    self._mcp_server.create_initialization_options(),
                )
            finally:
                tracker.transport_closed()
