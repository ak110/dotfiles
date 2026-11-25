from __future__ import annotations

import json
import re
import shlex
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from agent_toolkit._agents_server import wait_summary
from agent_toolkit._common.agents_server_tools import MCP_NAMESPACES
from session_evidence_extract import _load_records, _record_timestamp
from session_evidence_tool_calls_records import _record_tool_calls, _recorded_input, _recorded_result

_FIELD = re.compile(r"^\s*(?:-\s+)?(要求識別子|判定|確認段階|対象資料):[ \t]*(.*?)\s*$")
_WRAPPED_CALL = re.compile(r"\btools\.mcp__agents_server__(start|send_message|wait)\s*\(")
_WRAPPED_ARGUMENT = re.compile(r"""(?:["']?(subagent_md_path|handoff_from|session_id)["']?)\s*:\s*["']([^"'\r\n]+)["']""")

_SHELL_CALL = re.compile(
    r"(?:[ \t]*// @exec:[^\r\n]*\r?\n)?\s*"
    r"text\s*\(\s*await\s+tools\.(exec_command|write_stdin)\s*\(\s*\{([^{}]*)\}\s*\)\s*\)\s*;?"
)
_COMMAND_ARGUMENT = re.compile(r"""(?:^|,)\s*(?:cmd|"cmd"|'cmd')\s*:\s*(?:"([^"\r\n]*)"|'([^'\r\n]*)')\s*(?:,|$)""")
_SESSION_ARGUMENT = re.compile(r"""(?:^|,)\s*(?:session_id|"session_id"|'session_id')\s*:\s*([0-9]+)\s*(?:,|$)""")


class _Receipt(NamedTuple):
    timestamp: datetime
    session: str
    body: str
    order: int


class _Collected(NamedTuple):
    series: dict[str, str]
    receipts: list[_Receipt]
    requests: dict[str, list[tuple[datetime, str]]]


def _structured(value: object) -> object:
    for _ in range(12):
        if isinstance(value, dict):
            if value.get("isError") or value.get("error"):
                return None
            for key in ("structuredContent", "output", "content", "text"):
                if key in value:
                    value = value[key]
                    break
            else:
                return value
        elif isinstance(value, list) and len(value) == 1:
            value = value[0]
        elif isinstance(value, str):
            if value.startswith("Script completed\n") and "\nOutput:\n" in value:
                value = value.split("\nOutput:\n", 1)[1]
                continue
            try:
                value = json.loads(value)
            except ValueError, RecursionError:
                return value
        else:
            return value
    return None


def _bodies(value: object) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        value = value.get("sessions", [value])
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict) and row.get("status") == "completed":
                session, body = row.get("session_id"), row.get("agent_message")
                if isinstance(session, str) and isinstance(body, str):
                    yield session, body
    if not isinstance(value, str):
        return
    completed = None
    lines = iter(value.splitlines())
    for line in lines:
        if line.startswith(wait_summary.TERMINAL_LINE_PREFIX):
            match = re.match(
                rf"{re.escape(wait_summary.TERMINAL_LINE_PREFIX)}session_id=(\S+) .*?\sstatus=([a-z_]+)(?:\s|$)",
                line,
            )
            completed = match[1] if match and match[2] == "completed" else None
        elif line.startswith(wait_summary.WAIT_BODY_START_PREFIX):
            session = line.removeprefix(wait_summary.WAIT_BODY_START_PREFIX)
            body = []
            for inner in lines:
                if inner == wait_summary.WAIT_BODY_END_PREFIX + session:
                    if session == completed:
                        yield session, "\n".join(body)
                    break
                body.append(inner)
            completed = None


def _operation(tool: str, command: str) -> str:
    for prefix in MCP_NAMESPACES:
        if tool.startswith(prefix):
            return tool.removeprefix(prefix)
    if tool.rsplit("__", 1)[-1] not in {"Bash", "exec_command", "CommandExecution"}:
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if (
        len(tokens) >= 3
        and tokens[0].replace("\\", "/").rsplit("/", 1)[-1] in {"atk", "atk.cmd", "atk.exe"}
        and tokens[1:3] == ["agents", "wait"]
    ):
        return "wait"
    return ""


def _exec_result(raw: object) -> dict | None:
    if not isinstance(raw, str) or not raw.startswith("Script completed\n") or "\nOutput:\n" not in raw:
        return None
    try:
        result = json.loads(raw.split("\nOutput:\n", 1)[1])
    except ValueError, RecursionError:
        return None
    return result if isinstance(result, dict) and not (result.get("error") or result.get("isError")) else None


def collect(roots: list[Path]) -> _Collected:
    parents: dict[str, str] = {}
    reviewers: set[str] = set()
    receipts: dict[tuple[datetime, str, str], _Receipt] = {}
    requests: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    paths = {path.resolve() for root in roots for path in ([root] if root.is_file() else root.rglob("*.jsonl"))}
    for path in sorted(paths):
        records = _load_records(str(path)) or []
        entries = {record.line: record for record in records}
        pending: dict[str, list[str]] = {}
        deliveries: dict[str, int] = defaultdict(int)
        continued: set[tuple[str, str, int]] = set()
        path_receipts: list[tuple[datetime, str, str, int]] = []
        for call in sorted(_record_tool_calls(records), key=lambda item: item.result_line or item.line):
            entry = entries[call.line].entry
            args = _recorded_input(entry, call.call_id)
            command = args.get("command", args.get("cmd", call.text))
            operation = _operation(call.tool, command if isinstance(command, str) else "")
            shell_call = _SHELL_CALL.fullmatch(call.text.strip()) if call.tool == "functions.exec" else None
            poll = shell_call is not None and shell_call[1] == "write_stdin"
            if shell_call:
                argument = _COMMAND_ARGUMENT.search(shell_call[2])
                command = (argument[1] or argument[2] or "") if argument else ""
                operation = "wait" if poll else _operation("exec_command", command)
            elif call.tool.rsplit(".", 1)[-1] == "exec":
                operations = _WRAPPED_CALL.findall(call.text)
                if len(operations) == 1:
                    operation = operations[0]
                    args = dict(_WRAPPED_ARGUMENT.findall(call.text))
                elif not operations and "tools.write_stdin" not in call.text and re.search(r"\bagents\s+wait\b", call.text):
                    operation = "wait"
            if operation not in {"start", "send_message", "wait"} or call.result_line is None:
                continue
            result = entries[call.result_line]
            payload = result.entry.get("payload")
            output = payload.get("output") if isinstance(payload, dict) else None
            raw = _recorded_result(result.entry, call.call_id)
            message = result.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("tool_use_id") == call.call_id and block.get("is_error")
                for block in content
            ):
                continue
            meta = result.entry.get("mcpMeta")
            structured = meta.get("structuredContent") if isinstance(meta, dict) else None
            value = _structured(structured if structured is not None else raw if raw is not None else output)
            if shell_call:
                execution = _exec_result(raw if raw is not None else output)
                argument = _SESSION_ARGUMENT.search(shell_call[2]) if poll else None
                process = argument[1] if argument else None
                if poll and process not in pending:
                    continue
                if execution is None:
                    if process is not None:
                        pending.pop(process, None)
                    continue
                running = execution.get("session_id")
                exit_code = execution.get("exit_code")
                chunk = execution.get("output")
                if (
                    isinstance(running, int)
                    and not isinstance(running, bool)
                    and exit_code is None
                    and (not poll or str(running) == process)
                ):
                    chunks = pending.setdefault(str(running), [])
                    if isinstance(chunk, str):
                        chunks.append(chunk)
                    continue
                chunks = pending.pop(process) if process is not None else []
                if (
                    not isinstance(exit_code, int)
                    or isinstance(exit_code, bool)
                    or exit_code != 0
                    or running is not None
                    or not isinstance(chunk, str)
                ):
                    continue
                value = "".join([*chunks, chunk])
            if value is None:
                continue
            if operation == "start" and isinstance(value, dict) and isinstance(session := value.get("session_id"), str):
                role = args.get("subagent_md_path")
                if isinstance(role, str) and role.replace("\\", "/").rsplit("/", 1)[-1] in {
                    "wi-draft-review.subagent.md",
                    "wi-draft-review",
                }:
                    reviewers.add(session)
                if isinstance(parent := args.get("handoff_from"), str):
                    parents[session] = parent
            timestamp = _record_timestamp(result)
            if timestamp is None:
                continue
            if operation in {"start", "send_message"} and isinstance(value, dict):
                session = (value if operation == "start" else args).get("session_id")
                prompt = args.get("prompt") or args.get("instruction") or args.get("extra_params")
                if isinstance(session, str) and (operation == "start" or value.get("delivery") in ("reply_started", "steered")):
                    deliveries[session] += 1
                if isinstance(session, str) and isinstance(prompt, (str, dict)):
                    requests[session].append(
                        (timestamp, prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False))
                    )
            if operation == "wait":
                for session, body in _bodies(value):
                    body = body.strip()
                    revision = deliveries[session]
                    path_receipts.append((timestamp, session, body, revision))
                    if poll:
                        continued.add((session, body, revision))
        seen: set[tuple[str, str, int]] = set()
        for timestamp, session, body, revision in path_receipts:
            identity = (session, body, revision)
            if identity in continued and identity in seen:
                continue
            seen.add(identity)
            key = (timestamp, session, body)
            receipts.setdefault(key, _Receipt(*key, len(receipts)))
    series = {}
    for session in reviewers | parents.keys():
        chain = [session]
        while chain[-1] in parents and parents[chain[-1]] not in chain:
            chain.append(parents[chain[-1]])
        if reviewers.intersection(chain):
            series[session] = chain[-1]
    return _Collected(series, sorted(receipts.values(), key=lambda row: (row.timestamp, row.order)), requests)


def _judgments(body: str) -> Iterator[dict[str, str]]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        match = _FIELD.fullmatch(line)
        if not match:
            continue
        name, value = match.groups()
        if name == "要求識別子" or name == "判定" and "判定" in fields:
            yield fields
            fields = {}
        fields[name] = value.strip("`")
    yield fields


def _request_identity(collected: _Collected, receipt: _Receipt, path: str, ids: set[str]) -> str | None:
    candidates = [(time, prompt) for time, prompt in collected.requests.get(receipt.session, ()) if time <= receipt.timestamp]
    if not candidates:
        return None
    latest = max(time for time, _ in candidates)
    prompts = {prompt for time, prompt in candidates if time == latest}
    if len(prompts) != 1:
        return None
    prompt = next(iter(prompts))
    if path not in prompt:
        return None
    matches = {
        match[2].strip("`") for line in prompt.splitlines() if (match := _FIELD.fullmatch(line)) and match[1] == "要求識別子"
    }
    return next(iter(matches)) if len(matches) == 1 and matches <= ids else None


def period(collected: _Collected, since: datetime, until: datetime) -> dict[str, int | float | str]:
    counts = dict.fromkeys(("passed", "failed", "first_pass", "no_second", "missing_path", "unassigned"), 0)
    by_path = defaultdict(list)
    for receipt in collected.receipts:
        if receipt.session not in collected.series:
            continue
        for fields in _judgments(receipt.body):
            verdict = fields.get("判定")
            if fields.get("確認段階") != "本文" or verdict not in {"合格", "修正要"}:
                continue
            path = fields.get("対象資料", "")
            if not path or path == "なし":
                counts["missing_path"] += int(since <= receipt.timestamp <= until)
                continue
            by_path[collected.series[receipt.session], path].append((receipt, fields.get("要求識別子"), verdict))
    for (_, path), rows in by_path.items():
        ids = {identity for _, identity, _ in rows if identity}
        tracks = defaultdict(list)
        for receipt, identity, verdict in rows:
            if not identity and len(ids) > 1:
                identity = _request_identity(collected, receipt, path, ids)
                if identity is None:
                    counts["unassigned"] += int(since <= receipt.timestamp <= until)
                    continue
            tracks[identity or next(iter(ids), None)].append((receipt.timestamp, verdict))
        for track in tracks.values():
            first, verdict = track[0]
            if not since <= first <= until:
                continue
            if verdict == "合格":
                counts["first_pass"] += 1
            elif len(track) < 2:
                counts["no_second"] += 1
            else:
                counts["passed" if track[1][1] == "合格" else "failed"] += 1
    denominator = counts["passed"] + counts["failed"]
    return {**counts, "pass_rate": counts["passed"] / denominator if denominator else "判定不能"}
