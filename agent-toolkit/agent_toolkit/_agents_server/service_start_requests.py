from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.types import CallToolRequestParams, CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.input_validation import validate_cwd
from agent_toolkit._agents_server.mcp_transport import AgentsServerMCP
from agent_toolkit._agents_server.service_transport import process_alive
from agent_toolkit._common import file_lock
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger(__name__)
_USE_SAME_ID = "同じ要求IDで`start_status`を照会する。成否不明の要求を別のIDで起動し直さない"


class StartRequests:
    def __init__(self, directory: pathlib.Path, process: str) -> None:
        self.directory = directory / "start-requests"
        self.process = process
        self.tasks: set[asyncio.Task[None]] = set()

    async def call(
        self,
        owner: str,
        params: CallToolRequestParams,
        start: Callable[[CallToolRequestParams], Awaitable[Any]],
    ) -> CallToolResult:
        inputs = params.arguments or {}
        request_id = inputs.get("request_id")
        if not isinstance(request_id, str) or not _valid_request_id(request_id):
            raise ActionableError("request_idには標準形式のUUID v4が必要です", next_action=_USE_SAME_ID)
        path = self.directory / request_id
        if params.name == "start_status":
            if set(inputs) != {"request_id"}:
                raise ActionableError("start_statusの引数はrequest_idだけです", next_action=_USE_SAME_ID)
            record = await run_file_operation(_read, path)
            return _response(request_id, record if record is not None and record["owner"] == owner else None)

        arguments = inputs.get("arguments")
        if set(inputs) != {"request_id", "arguments"} or not isinstance(arguments, dict) or "handoff_from" in arguments:
            raise ActionableError(
                "start_submitにはrequest_idと新規startのargumentsが必要です",
                next_action="引継ぎには同期のstartを使う。新規起動はstartのスキーマに従ってargumentsを指定する",
            )
        cwd = arguments.get("cwd")
        validate_cwd(cwd if isinstance(cwd, str) else "")
        receipt: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        request = params.model_copy(update={"name": "start", "arguments": arguments})
        # MCP側だけの取消を受理と起動へ伝えない。pyfltr: allow-comment
        task = asyncio.create_task(self._submit(owner, path, request, start, receipt))
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        try:
            return _response(request_id, await asyncio.shield(receipt))
        except asyncio.CancelledError:
            receipt.cancel()
            raise

    async def _submit(
        self,
        owner: str,
        path: pathlib.Path,
        params: CallToolRequestParams,
        start: Callable[[CallToolRequestParams], Awaitable[Any]],
        receipt: asyncio.Future[dict],
    ) -> None:
        arguments = params.arguments or {}
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        try:
            record, created = await run_file_operation(self._reserve, owner, path, digest, arguments["cwd"])
        except asyncio.CancelledError:
            receipt.cancel()
            raise
        except Exception as error:
            if not receipt.done():
                receipt.set_exception(error)
            return
        if not receipt.done():
            receipt.set_result(record)
        if created:
            await self._run(path, record, params, start)

    def _reserve(self, owner: str, path: pathlib.Path, digest: str, cwd: str) -> tuple[dict, bool]:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "reserve.lock").open("a+", encoding="utf-8") as lock:
            file_lock.acquire_lock(lock)
            try:
                existing = _read(path)
                if existing is not None:
                    if existing["owner"] != owner:
                        raise ActionableError("この要求IDは利用できません", next_action="自身で生成した要求IDを指定する")
                    if existing["digest"] != digest:
                        raise ActionableError("同じ要求IDが異なる入力で受付済みです", next_action=_USE_SAME_ID)
                    return existing, False
                record = {"owner": owner, "digest": digest, "process": self.process, "state": "pending", "cwd": cwd}
                atomic_write(path, json.dumps(record))
                return record, True
            finally:
                file_lock.release_lock(lock)

    async def _run(
        self,
        path: pathlib.Path,
        record: dict,
        params: CallToolRequestParams,
        start: Callable[[CallToolRequestParams], Awaitable[Any]],
    ) -> None:
        try:
            result = await start(params)
            if isinstance(result, CallToolResult) and result.is_error:
                record = {
                    **record,
                    "state": "failed",
                    "diagnostics": [item.text for item in result.content if isinstance(item, TextContent)],
                }
            elif isinstance(result, CallToolResult) and (data := result.structured_content) and data.get("session_id"):
                record = {**record, "state": "started", "result": data}
            else:
                record = {**record, "state": "unknown"}
        except Exception:
            _LOG.exception("非同期起動の成否を確定できません")
            record = {**record, "state": "unknown"}
        await run_file_operation(atomic_write, path, json.dumps(record))

    def _finished(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error("非同期起動の結果を保存できません", exc_info=error)

    async def close(self) -> None:
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _valid_request_id(value: str) -> bool:
    if len(value) != 36:
        return False
    try:
        identifier = uuid.UUID(value)
    except ValueError:
        return False
    return identifier.version == 4 and str(identifier) == value


def _read(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return None


def _response(request_id: str, record: dict | None) -> CallToolResult:
    value = {"request_id": request_id}
    if record is None:
        value["request_status"] = "not_found"
    else:
        state = record["state"]
        if state == "pending" and not process_alive(record["process"]):
            state = "unknown"
        value.update(request_status=state, cwd=record["cwd"])
        value.update(record.get("result", {}))
        if state == "failed":
            value["diagnostics"] = record["diagnostics"]
        elif state == "unknown":
            value["next_action"] = _USE_SAME_ID
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))], structured_content=value
    )


def register_start_request_tools(mcp: AgentsServerMCP) -> None:
    @mcp.tool(
        name="start_submit",
        title="委譲起動の非同期受付",
        description="起動枠やバックエンドを待たずに受付する。同じID・同じ入力を再提示しても再起動しない。",
        annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True),
        structured_output=True,
    )
    async def start_submit(
        request_id: Annotated[str, Field(description="呼出元が起動前に生成・保持するUUID v4（標準形式）")],
        arguments: Annotated[
            dict[str, Any], Field(description="startと同じ新規起動の引数（絶対cwdが必須。handoff_fromは不可）")
        ],
    ) -> dict[str, Any]:
        raise RuntimeError("非同期受付は管理サービス経由の接続で使ってください")

    @mcp.tool(
        name="start_status",
        title="非同期受付の起動結果",
        description=(
            "同じ会話の起動結果を照会する。request_statusはnot_found、pending、started、failed、unknownのいずれか。"
            "startedならstartと同じsession_idとroot_session_id（ある場合）を終了待機へ渡す。"
        ),
        annotations=ToolAnnotations(read_only_hint=True),
        structured_output=True,
    )
    async def start_status(
        request_id: Annotated[str, Field(description="呼出元がstart_submitの前に保持したUUID v4（標準形式）")],
    ) -> dict[str, Any]:
        raise RuntimeError("非同期受付の照会は管理サービス経由の接続で使ってください")
