from __future__ import annotations

import logging
import pathlib
from collections.abc import Callable, Mapping
from typing import Any

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_requests_inputs import named_input_lines
from agent_toolkit._atk import managed_temp

_LOG = logging.getLogger(__name__)

RECORD_NAME = "work-contract.md"

_HEADER = (
    "# 担当の作業契約\n"
    "\n"
    "作業が長引いた時や会話の圧縮の後に、この担当の目的を見失わないよう見返すための記録。\n"
    "agents_serverが起動依頼と配送した追送から作る。"
    "担当が追記するのは、ここに無い依頼の訂正（会話で受けたものなど）だけとする。\n"
    "進捗と再開位置は引き継ぎ記録、上位の目的と制約は全体作業契約に置く。\n"
)
_FOLLOWUPS_HEADING = "## 追送（配送の順）"


def task_request(subagent_md_path: str, inputs: Mapping[str, str]) -> str:
    role = pathlib.PurePath(subagent_md_path).name.removesuffix(".subagent.md")
    return "\n".join([f"役割: {role}", "入力:", *named_input_lines(dict(inputs))])


def _path(session_id: str) -> pathlib.Path:
    return managed_temp.resolve_session_temp(session_id) / RECORD_NAME


def create(session_id: str, request: str) -> str | None:
    try:
        path = _path(session_id)
        path.write_text(
            f"{_HEADER}\n## 起動依頼\n\n{request.strip()}\n\n{_FOLLOWUPS_HEADING}\n", encoding="utf-8", newline="\n"
        )
    except (managed_temp.ManagedTempError, OSError) as error:
        _LOG.warning("委譲先の作業契約を作成できない: session_id=%s", session_id, exc_info=True)
        return f"委譲先の作業契約を作成できない（{type(error).__name__}: {error}）。担当は起動済みである。"
    return None


def inherit(predecessor_id: str, session_id: str) -> str | None:
    source = managed_temp.find_session_temp(predecessor_id)
    if source is None or not (source / RECORD_NAME).is_file():
        return None
    try:
        _path(session_id).write_bytes((source / RECORD_NAME).read_bytes())
    except (managed_temp.ManagedTempError, OSError) as error:
        _LOG.warning("前任の作業契約を引き継げない: %s -> %s", predecessor_id, session_id, exc_info=True)
        return f"前任の作業契約を後任へ引き継げない（{type(error).__name__}: {error}）。"
    return None


def append_followup(session_id: str, prompt: str, delivery: str) -> None:
    if delivery == "reply_failed":
        return
    directory = managed_temp.find_session_temp(session_id)
    path = directory / RECORD_NAME if directory is not None else None
    if path is None or not path.is_file():
        return
    try:
        body = path.read_text(encoding="utf-8")
        count = body.count("\n### 追送")
        status = "配送結果が不明" if "ambiguous" in delivery else "配送確定"
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"\n### 追送{count + 1}（{status}）\n\n{prompt.strip()}\n")
    except OSError, UnicodeError:
        _LOG.warning("委譲先の作業契約へ追送を記録できない: session_id=%s", session_id, exc_info=True)


async def _record(response: dict[str, Any], operation: Callable[..., str | None], *args: str) -> None:
    warning = await run_file_operation(operation, *args)
    if warning is not None:
        response.setdefault("warnings", []).append({"scope": "work-contract", "message": warning})


async def record_created(response: dict[str, Any], request: str) -> None:
    await _record(response, create, response["session_id"], request)


async def record_inherited(response: dict[str, Any], predecessor_id: str) -> None:
    await _record(response, inherit, predecessor_id, response["session_id"])
