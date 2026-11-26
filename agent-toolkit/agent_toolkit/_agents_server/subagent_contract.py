from __future__ import annotations

import logging
import pathlib
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_requests_inputs import named_input_lines
from agent_toolkit._atk import managed_temp
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.message_format import auto_message

_LOG = logging.getLogger(__name__)
_UPDATE_LOCK = threading.Lock()

RECORD_NAME = "work-contract.md"
_STARTUP_REQUEST = "startup-request.md"

_HEADER = (
    "# 担当の作業契約\n"
    "\n"
    "作業が長引いた時や会話の圧縮の後に、この担当の目的を見失わないよう見返すための記録。\n"
    "サーバーが起動依頼を記録し、追送時に指定された差分だけを反映する。"
    "差分の指定がない訂正を受けた担当は、該当箇所だけを書き換える。\n"
    "進捗と再開位置は引き継ぎ記録、上位の目的と制約は全体作業契約に置く。\n"
)
_PURPOSE = "## 目的"
_COMPLETION = "## 完遂事項"
_CONSTRAINTS = "## 守る制約"
_UNCONFIRMED = "\n**配送結果が不明な変更（現行の依頼ではない）**\n\n"
_INITIALIZE = f"[`{_STARTUP_REQUEST}`]({_STARTUP_REQUEST})から着手時に一度だけ、この契約の3区分を記入する。"


def task_request(subagent_md_path: str, inputs: Mapping[str, str]) -> str:
    role = pathlib.PurePath(subagent_md_path).name.removesuffix(".subagent.md")
    return "\n".join([f"役割: {role}", "入力:", *named_input_lines(dict(inputs))])


def mark_contract_update(prompt: str) -> str:
    return f"{prompt}\n\n" + auto_message(
        "サーバーがこの追送の差分を作業契約へ反映する。同じ変更を自分で重ねて書かないでください。",
        source="agents-server",
        kind="work-contract-update",
    )


def _path(session_id: str) -> pathlib.Path:
    return managed_temp.resolve_session_temp(session_id) / RECORD_NAME


def create(session_id: str, request: str) -> str | None:
    try:
        path = _path(session_id)
        request = request.strip()
        headings = [line for line in request.splitlines() if line.startswith("## ")]
        if headings != [_PURPOSE, _COMPLETION, _CONSTRAINTS] or not request.startswith(_PURPOSE + "\n"):
            atomic_write(path.with_name(_STARTUP_REQUEST), request + "\n")
            request = f"{_PURPOSE}\n\n{_INITIALIZE}\n\n{_COMPLETION}\n\n{_CONSTRAINTS}"
        atomic_write(path, f"{_HEADER}\n{request}\n")
    except (managed_temp.ManagedTempError, OSError) as error:
        _LOG.warning("委譲先の作業契約を作成できない: session_id=%s", session_id, exc_info=True)
        return f"委譲先の作業契約を作成できない（{type(error).__name__}: {error}）。担当は起動済みである。"
    return None


def inherit(predecessor_id: str, session_id: str) -> str | None:
    source = managed_temp.find_session_temp(predecessor_id)
    if source is None or not (source / RECORD_NAME).is_file():
        return None
    try:
        destination = _path(session_id)
        if (startup := source / _STARTUP_REQUEST).is_file():
            atomic_write(destination.with_name(_STARTUP_REQUEST), startup.read_bytes())
        atomic_write(destination, (source / RECORD_NAME).read_bytes())
    except (managed_temp.ManagedTempError, OSError) as error:
        _LOG.warning("前任の作業契約を引き継げない: %s -> %s", predecessor_id, session_id, exc_info=True)
        return f"前任の作業契約を後任へ引き継げない（{type(error).__name__}: {error}）。"
    return None


def _edit_request(active: str, changes: Mapping[str, str], *, structured: bool = False) -> str:
    edits: list[tuple[int, int, str]] = []
    for old, new in changes.items():
        if old:
            start = active.find(old)
            if start < 0 or active.find(old, start + 1) >= 0:
                raise ValueError("置換・取消の元の文が現在の依頼に一意に存在しない")
            edits.append((start, start + len(old), new))
    right = len(active)
    for start, end, new in sorted(edits, reverse=True):
        if end > right:
            raise ValueError("置換・取消の対象が同じ箇所に重なっている")
        active = f"{active[:start]}{new}{active[end:]}"
        right = start
    addition = changes.get("")
    if not addition:
        return active
    if structured:
        tasks, heading, constraints = active.partition("\n" + _CONSTRAINTS)
        if not heading:
            raise ValueError("守る制約の見出しが無い")
        return f"{tasks.rstrip()}\n\n{addition}\n\n{heading.lstrip()}{constraints}"
    return f"{active.rstrip()}\n\n{addition}"


def apply_followup(session_id: str, changes: Mapping[str, str], delivery: str) -> str | None:
    if not changes or delivery not in {"steered", "reply_started", "reply_ambiguous"}:
        return None
    try:
        directory = managed_temp.find_session_temp(session_id)
        if directory is None:
            raise ValueError("担当の一時領域が無い")
        path = directory / RECORD_NAME
        with _UPDATE_LOCK:
            body = path.read_text(encoding="utf-8")
            if not body.startswith(_HEADER):
                raise ValueError("現行の依頼を持つ作業契約ではない")
            unresolved, marker, active = body.removeprefix(_HEADER).lstrip("\n").partition(_PURPOSE + "\n")
            if not marker:
                raise ValueError("目的の見出しが無い")
            entry = "- " + "、".join(f"{old!r} → {new!r}" for old, new in sorted(changes.items()))
            pending = unresolved.removeprefix(_UNCONFIRMED.lstrip("\n")).strip().splitlines()
            if delivery == "reply_ambiguous":
                if entry not in pending:
                    pending.append(entry)
            else:
                if active.startswith("\n" + _INITIALIZE + "\n"):
                    startup = path.with_name(_STARTUP_REQUEST)
                    atomic_write(startup, _edit_request(startup.read_text(encoding="utf-8"), changes).strip() + "\n")
                else:
                    active = _edit_request(active, changes, structured=True)
                pending = [line for line in pending if line != entry]
            unresolved = _UNCONFIRMED + "\n".join(pending) + "\n" if pending else ""
            updated = f"{_HEADER}{unresolved}\n{marker}{active.rstrip()}\n"
            if updated != body:
                atomic_write(path, updated)
    except (managed_temp.ManagedTempError, OSError, ValueError) as error:
        _LOG.warning("委譲先の作業契約を更新できない: session_id=%s", session_id, exc_info=True)
        return f"委譲先の作業契約を更新できない（{error}）。追送の配送結果はdeliveryを確認し、契約だけを更新する。"
    return None


async def _record(response: dict[str, Any], operation: Callable[..., str | None], *args: str) -> None:
    warning = await run_file_operation(operation, *args)
    if warning is not None:
        response.setdefault("warnings", []).append({"scope": "work-contract", "message": warning})


async def record_created(response: dict[str, Any], request: str) -> None:
    await _record(response, create, response["session_id"], request)


async def record_inherited(response: dict[str, Any], predecessor_id: str) -> None:
    await _record(response, inherit, predecessor_id, response["session_id"])


async def record_followup(response: dict[str, Any], session_id: str, changes: Mapping[str, str]) -> None:
    warning = await run_file_operation(apply_followup, session_id, changes, response["delivery"])
    if warning is not None:
        response.setdefault("warnings", []).append({"scope": "work-contract", "message": warning})
