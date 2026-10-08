"""SDKのツール登録と公開stdio接続を結び、初期化とツールエラーを診断する。"""

from __future__ import annotations

import functools
import inspect
import logging
import typing
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    Icon,
    InputRequiredResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    ToolAnnotations,
)

from agent_toolkit._agents_server.session_errors import DelegateBackendError, SessionInitializationTimeoutError
from agent_toolkit._agents_server.tool_descriptions import KIND_MCP_TOOL, schema_text
from agent_toolkit._common.next_action import ActionableError, with_next_action

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


class _InitializationLogTracker:
    """initialize要求と対応応答の節目だけを永続ログへ記録する。"""

    def __init__(self) -> None:
        self._pending_request_ids: set[str | int] = set()

    def receive(self, message: SessionMessage | Exception) -> None:
        root = getattr(message, "message", None)
        if getattr(root, "method", None) != "initialize":
            return
        request_id = getattr(root, "id", None)
        if not isinstance(request_id, (str, int)):
            return
        self._pending_request_ids.add(request_id)
        _LOG.info("MCP initializeを受信しました: request_id=%s", request_id)

    def sent(self, message: SessionMessage) -> None:
        root = message.message
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        error = getattr(root, "error", None)
        if error is not None:
            _LOG.error(
                "MCP initialize応答が失敗しました: request_id=%s exception_type=%s exception=%s",
                request_id,
                type(error).__name__,
                getattr(error, "message", error),
            )
            return
        _LOG.info("MCP initialize応答が完了しました: request_id=%s", request_id)

    def send_failed(self, message: SessionMessage, exc: BaseException) -> None:
        root = message.message
        request_id = getattr(root, "id", None)
        if request_id not in self._pending_request_ids:
            return
        self._pending_request_ids.remove(request_id)
        _LOG.error(
            "MCP initialize応答の送信に失敗しました: request_id=%s exception_type=%s exception=%s",
            request_id,
            type(exc).__name__,
            exc,
        )

    def transport_closed(self) -> None:
        for request_id in sorted(self._pending_request_ids, key=str):
            _LOG.error(
                "MCP initializeが未完了のままtransportが終了しました: "
                "request_id=%s exception_type=RuntimeError exception=transport closed before initialize response",
                request_id,
            )
        self._pending_request_ids.clear()


class _ReceiveStream(typing.Protocol):
    """SDKとanyioに共通する受信streamの公開操作。"""

    async def receive(self) -> SessionMessage | Exception: ...
    async def aclose(self) -> None: ...


class _SendStream(typing.Protocol):
    """SDKとanyioに共通する送信streamの公開操作。"""

    async def send(self, item: SessionMessage) -> None: ...
    async def aclose(self) -> None: ...


class _InitializationLoggingReceiveStream(ObjectReceiveStream[SessionMessage | Exception]):
    def __init__(
        self,
        stream: _ReceiveStream,
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
    def __init__(self, stream: _SendStream, tracker: _InitializationLogTracker) -> None:
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

    共通の例外型の`str()`は理由だけを返すため、ここで次の操作を加える。
    入力スキーマはMCPServerが`__wrapped__`の署名から生成する。
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


class AgentsServerMCP(MCPServer[None]):
    """SDKに登録を任せ、診断streamとManagerのlifespanを公開transportへ接続する。"""

    def __init__(
        self,
        name: str,
        *,
        instructions: str,
        lifespan: Callable[[MCPServer[None]], AbstractAsyncContextManager[None]],
    ) -> None:
        super().__init__(name, instructions=instructions)
        self._agents_lifespan = lifespan
        self._agents_name = name
        self._agents_instructions = instructions

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

    async def _list_registered_tools(
        self, _context: ServerRequestContext[None], _params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=await self.list_tools())

    async def _call_registered_tool(
        self, _context: ServerRequestContext[None], params: CallToolRequestParams
    ) -> CallToolResult | InputRequiredResult:
        try:
            return await self.call_tool(params.name, params.arguments or {})
        except ToolError as error:
            text = str(error)
            if "次の操作:" not in text:
                text = with_next_action(text, "ツールの説明で引数の受理形式を確かめて再発行する")
            return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)

    @typing.override
    async def run_stdio_async(self) -> None:
        server = Server(
            self._agents_name,
            instructions=self._agents_instructions,
            lifespan=lambda _server: self._agents_lifespan(self),
            on_list_tools=self._list_registered_tools,
            on_call_tool=self._call_registered_tool,
        )
        tracker = _InitializationLogTracker()
        async with stdio_server() as (read_stream, write_stream):
            try:
                await server.run(
                    _InitializationLoggingReceiveStream(read_stream, tracker),
                    _InitializationLoggingSendStream(write_stream, tracker),
                    server.create_initialization_options(),
                )
            finally:
                tracker.transport_closed()
