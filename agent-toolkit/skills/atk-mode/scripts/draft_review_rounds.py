from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

import filelock
from agent_toolkit._agents_server import wait_summary
from agent_toolkit._common import derived_index, state_paths
from agent_toolkit._common.agents_server_tools import MCP_NAMESPACES
from session_evidence_extract import _load_records, _record_timestamp
from session_evidence_tool_calls_records import _record_tool_calls, _recorded_input, _recorded_result

_FIELD = re.compile(r"^\s*(?:-\s+)?(要求識別子|判定|確認段階|対象資料):[ \t]*(.*?)\s*$")
_WRAPPED_CALL = re.compile(r"\btools\.mcp__agents_server__(start|send_message|wait)\s*\(")
_WRAPPED_ARGUMENT = re.compile(
    r"""(?:["']?(subagent_md_path|handoff_from|session_id|instruction|原稿|要求識別子)["']?)\s*:\s*"""
    r"""("(?:[^"\\\r\n]|\\.)*"|'(?:[^'\\\r\n]|\\.)*')"""
)

_DRAFT_ROLE = "wi-draft-review"
OTHER_ROLES = ("plan-review", "reader-fit-review", "usability-review", "external-write-review")
_OTHER_FIELDS = re.compile(
    r"^(?:- )?(?:判定の回数|判定|未解決の指摘数|成果物|再レビュー|状態|レビューしたHEAD|対象資料|区分|新しい要件):[^\n]*$",
    re.MULTILINE,
)

_SHELL_CALL = re.compile(
    r"(?:[ \t]*// @exec:[^\r\n]*\r?\n)?\s*"
    r"text\s*\(\s*await\s+tools\.(exec_command|write_stdin)\s*\(\s*\{([^{}]*)\}\s*\)\s*\)\s*;?"
)
_COMMAND_ARGUMENT = re.compile(r"""(?:^|,)\s*(?:cmd|"cmd"|'cmd')\s*:\s*(?:"([^"\r\n]*)"|'([^'\r\n]*)')\s*(?:,|$)""")
_SESSION_ARGUMENT = re.compile(r"""(?:^|,)\s*(?:session_id|"session_id"|'session_id')\s*:\s*([0-9]+)\s*(?:,|$)""")


class _Receipt(NamedTuple):
    timestamp: datetime
    session: str
    digest: str
    judgments: list[dict[str, str]]
    body: str
    order: int


class _Request(NamedTuple):
    timestamp: datetime
    digest: str
    identity: str | None
    prompt: str | None
    rejudgment: bool


class _Record(NamedTuple):
    parents: dict[str, str]
    reviewers: dict[str, str]
    receipts: list[tuple[datetime, str, str, int, list[dict[str, str]], str]]
    requests: dict[str, list[_Request]]


class _Collected(NamedTuple):
    series: dict[str, str]
    receipts: list[_Receipt]
    requests: dict[str, list[_Request]]
    roles: dict[str, str]


class _SavedWait(NamedTuple):
    stderr: str | None
    timestamp: datetime
    deliveries: dict[str, int]


def _wrapped_arguments(source: str) -> dict[str, object]:
    fields: dict[str, str] = {}
    for key, literal in _WRAPPED_ARGUMENT.findall(source):
        if literal.startswith('"'):
            try:
                fields[key] = json.loads(literal)
            except ValueError:
                continue
        else:
            fields[key] = re.sub(r"\\([\\'])", r"\1", literal[1:-1])
    params = {key: fields.pop(key) for key in ("原稿", "要求識別子") if key in fields}
    return {**fields, "extra_params": params}


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


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _request(timestamp: datetime, prompt: str) -> _Request:
    matches = {
        match[2].strip("`") for line in prompt.splitlines() if (match := _FIELD.fullmatch(line)) and match[1] == "要求識別子"
    }
    identity = next(iter(matches)) if len(matches) == 1 else None
    params = _request_fields(prompt)
    if params.get("instruction") == "再判定せよ":
        selected = params.get("要求識別子")
        identity = selected if isinstance(selected, str) else None
        return _Request(timestamp, _hash_text(prompt), identity, prompt, True)
    return _Request(
        timestamp,
        _hash_text(prompt),
        identity,
        prompt if identity is not None else None,
        "再レビュー" in prompt or "再判定" in prompt,
    )


def _native_wait_value(
    command: str,
    entry: dict,
    redirects: dict[str, _SavedWait],
    *,
    is_wait: bool,
    timestamp: datetime | None,
    deliveries: dict[str, int],
) -> tuple[str, _SavedWait | None] | None:
    if not is_wait and (not redirects or not command.lstrip().startswith("cat ")):
        return None
    payload = entry.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    if not isinstance(item, dict) or item.get("type") != "CommandExecution" or item.get("exit_code") != 0:
        return None
    output = item.get("aggregated_output")
    if not isinstance(output, str):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if is_wait:
        if len(tokens) == 3:
            return output, None
        if (
            timestamp is not None
            and len(tokens) in (5, 8)
            and tokens[3] == ">"
            and (len(tokens) == 5 or tokens[5:7] == ["2", ">"])
            and Path(tokens[4]).is_absolute()
        ):
            redirects[tokens[4]] = _SavedWait(tokens[7] if len(tokens) == 8 else None, timestamp, dict(deliveries))
    elif (
        len(tokens) in (2, 5)
        and tokens[0] == "cat"
        and tokens[1] in redirects
        and (len(tokens) == 2 or (tokens[2:4] == [";", "cat"] and tokens[4] == redirects[tokens[1]].stderr))
    ):
        return output, redirects.pop(tokens[1])
    return None


def _extract(path: Path) -> _Record | None:
    records = _load_records(str(path))
    if records is None:
        return None
    parents: dict[str, str] = {}
    reviewers: dict[str, str] = {}
    requests: dict[str, list[_Request]] = defaultdict(list)
    entries = {record.line: record for record in records}
    pending: dict[str, list[str]] = {}
    deliveries: dict[str, int] = defaultdict(int)
    continued: set[tuple[str, str, int]] = set()
    redirects: dict[str, _SavedWait] = {}
    path_receipts: list[tuple[datetime, str, str, int, list[dict[str, str]], str]] = []
    for call in sorted(_record_tool_calls(records), key=lambda item: item.result_line or item.line):
        entry = entries[call.line].entry
        args = _recorded_input(entry, call.call_id)
        command = args.get("command", args.get("cmd", call.text))
        operation = _operation(call.tool, command if isinstance(command, str) else "")
        native_wait = (
            _native_wait_value(
                call.text,
                entry,
                redirects,
                is_wait=operation == "wait",
                timestamp=_record_timestamp(entries[call.line]),
                deliveries=deliveries,
            )
            if call.tool == "CommandExecution"
            else None
        )
        if native_wait is not None:
            operation = "wait"
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
                args = _wrapped_arguments(call.text)
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
            isinstance(block, dict) and block.get("tool_use_id") == call.call_id and block.get("is_error") for block in content
        ):
            continue
        meta = result.entry.get("mcpMeta")
        structured = meta.get("structuredContent") if isinstance(meta, dict) else None
        value = _structured(structured if structured is not None else raw if raw is not None else output)
        saved = None
        if native_wait is not None:
            value = _structured(native_wait[0])
            saved = native_wait[1]
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
            if isinstance(role, str):
                name = role.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".subagent.md")
                if name in (_DRAFT_ROLE, *OTHER_ROLES):
                    reviewers[session] = name
            if isinstance(parent := args.get("handoff_from"), str):
                parents[session] = parent
        timestamp = saved.timestamp if saved is not None else _record_timestamp(result)
        if timestamp is None:
            continue
        if operation in {"start", "send_message"} and isinstance(value, dict):
            session = (value if operation == "start" else args).get("session_id")
            instruction = args.get("instruction")
            if instruction == "再判定せよ" and isinstance(params := args.get("extra_params"), dict):
                if value.get("delivery") not in ("reply_started", "steered"):
                    continue
                prompt = {
                    "instruction": instruction,
                    **{key: params[key] for key in ("原稿", "要求識別子") if isinstance(params.get(key), str)},
                }
            else:
                prompt = args.get("prompt") or instruction or args.get("extra_params")
            if isinstance(session, str) and (operation == "start" or value.get("delivery") in ("reply_started", "steered")):
                deliveries[session] += 1
            if isinstance(session, str) and isinstance(prompt, (str, dict)):
                requests[session].append(
                    _request(timestamp, prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False))
                )
        if operation == "wait":
            for session, body in _bodies(value):
                body = body.strip()
                digest = _hash_text(body)
                revision = saved.deliveries.get(session, 0) if saved is not None else deliveries[session]
                path_receipts.append(
                    (
                        timestamp,
                        session,
                        digest,
                        revision,
                        [fields for fields in _judgments(body) if fields],
                        "\n".join(_OTHER_FIELDS.findall(body)),
                    )
                )
                if poll or native_wait is not None:
                    continued.add((session, digest, revision))
    receipts: list[tuple[datetime, str, str, int, list[dict[str, str]], str]] = []
    seen: set[tuple[str, str, int]] = set()
    for timestamp, session, digest, revision, judgments, body in path_receipts:
        identity = (session, digest, revision)
        if identity in continued and identity in seen:
            continue
        seen.add(identity)
        receipts.append((timestamp, session, digest, revision, judgments, body))
    return _Record(parents, reviewers, receipts, requests)


_INDEX_MAX_BYTES = 64 * 1024 * 1024
_CHECKPOINT_RECORDS = 256


def _encode(record: _Record) -> list[Any]:
    return [
        record.parents,
        record.reviewers,
        [
            [time.isoformat(), session, digest, revision, judgments, body]
            for time, session, digest, revision, judgments, body in record.receipts
        ],
        {
            session: [
                [time.isoformat(), digest, identity, prompt, rejudgment] for time, digest, identity, prompt, rejudgment in rows
            ]
            for session, rows in record.requests.items()
        },
    ]


def _string_map(value: Any) -> bool:
    return isinstance(value, dict) and all(isinstance(item, str) for item in value.values())


def _indexed_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("派生索引の時刻にオフセットがない")
    return parsed


def _decode_receipt(row: Any) -> tuple[datetime, str, str, int, list[dict[str, str]], str]:
    if not isinstance(row, list):
        raise ValueError("派生索引の返却が不正")
    time, session, digest, revision, judgments, body = row
    if (
        not isinstance(session, str)
        or not isinstance(digest, str)
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or not isinstance(judgments, list)
        or not isinstance(body, str)
        or not all(_string_map(item) for item in judgments)
    ):
        raise ValueError("派生索引の返却が不正")
    return _indexed_timestamp(time), session, digest, revision, judgments, body


def _decode_request(row: Any) -> _Request:
    if not isinstance(row, list):
        raise ValueError("派生索引の依頼が不正")
    time, digest, identity, prompt, rejudgment = row
    if (
        not isinstance(digest, str)
        or not isinstance(rejudgment, bool)
        or not (
            (
                identity is None
                and (prompt is None or (isinstance(prompt, str) and _request_fields(prompt).get("instruction") == "再判定せよ"))
            )
            or (isinstance(identity, str) and isinstance(prompt, str))
        )
    ):
        raise ValueError("派生索引の依頼が不正")
    return _Request(_indexed_timestamp(time), digest, identity, prompt, rejudgment)


def _decode(fields: list[Any]) -> _Record:
    parents, reviewers, receipts, requests = fields
    if (
        not _string_map(parents)
        or not _string_map(reviewers)
        or not isinstance(receipts, list)
        or not isinstance(requests, dict)
        or not all(isinstance(rows, list) for rows in requests.values())
    ):
        raise ValueError("派生索引の値が不正")
    return _Record(
        parents,
        reviewers,
        [_decode_receipt(row) for row in receipts],
        {session: [_decode_request(row) for row in rows] for session, rows in requests.items()},
    )


def _indexed_records(paths: list[Path], index: Path) -> dict[str, _Record]:
    listed = []
    unlisted = {}
    unlisted_count = 0
    for path in paths:
        try:
            listed.append((str(path), path, derived_index.identity(path.stat())))
        except OSError:
            unlisted_count += 1
            if (record := _extract(path)) is not None:
                unlisted[str(path)] = record
    checkpoint = derived_index.Checkpoint(every=_CHECKPOINT_RECORDS, max_bytes=_INDEX_MAX_BYTES)
    code = derived_index.code_version(__name__, script_root=Path(__file__).resolve().parent)
    index.parent.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(index.with_suffix(".lock"), timeout=3):
        records = derived_index.refreshed(
            index, code, listed, lambda _key, path: _extract(path), _encode, _decode, checkpoint=checkpoint
        )
    print(
        f"投入前レビュー索引: 解析={len(paths) if checkpoint.overflow else checkpoint.digested + unlisted_count}件、"
        f"再利用={0 if checkpoint.overflow else checkpoint.reused}件、"
        f"容量={0 if checkpoint.overflow else checkpoint.bytes_saved} bytes、上限={checkpoint.max_bytes} bytes",
        file=sys.stderr,
    )
    return records | unlisted


def index_path(roots: list[Path]) -> Path:
    key = _hash_text(json.dumps(sorted({str(root.resolve()) for root in roots})))
    return state_paths.state_dir() / "session-review" / "draft-review-index" / f"{key}.json"


def collect(roots: list[Path], *, index: Path | None = None) -> _Collected:
    parents: dict[str, str] = {}
    reviewers: dict[str, str] = {}
    receipts: dict[tuple[datetime, str, str], _Receipt] = {}
    requests: dict[str, list[_Request]] = defaultdict(list)
    paths = sorted({path.resolve() for root in roots for path in ([root] if root.is_file() else root.rglob("*.jsonl"))})
    indexed = None
    if index is not None:
        try:
            indexed = _indexed_records(paths, index)
        except (OSError, filelock.Timeout) as error:
            print(f"投入前レビュー索引を利用できないため全件を読む: {error}", file=sys.stderr)
    for path in paths:
        record = indexed.get(str(path)) if indexed is not None else _extract(path)
        if record is None:
            continue
        parents.update(record.parents)
        reviewers.update(record.reviewers)
        for session, rows in record.requests.items():
            requests[session].extend(rows)
        for timestamp, session, digest, _revision, judgments, body in record.receipts:
            key = (timestamp, session, digest)
            receipts.setdefault(key, _Receipt(*key, judgments, body, len(receipts)))
    series: dict[str, str] = {}
    roles: dict[str, str] = {}
    for session in reviewers.keys() | parents.keys():
        chain = [session]
        while chain[-1] in parents and parents[chain[-1]] not in chain:
            chain.append(parents[chain[-1]])
        chain_roles = {reviewers[item] for item in chain if item in reviewers}
        if len(chain_roles) == 1:
            series[session] = chain[-1]
            roles[session] = chain_roles.pop()
    return _Collected(series, sorted(receipts.values(), key=lambda row: (row.timestamp, row.order)), requests, roles)


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


def _latest_request(collected: _Collected, receipt: _Receipt) -> _Request | None:
    candidates = [row for row in collected.requests.get(receipt.session, ()) if row.timestamp <= receipt.timestamp]
    if not candidates:
        return None
    latest = max(row.timestamp for row in candidates)
    prompts = {row.digest: row for row in candidates if row.timestamp == latest}
    return next(iter(prompts.values())) if len(prompts) == 1 else None


def _request_fields(prompt: str) -> dict[str, object]:
    try:
        params = json.loads(prompt)
    except ValueError:
        return {}
    return params if isinstance(params, dict) else {}


def _request_identity(
    prompt: str, params: dict[str, object], path: str, path_ids: set[str], series_ids: set[str]
) -> str | None:
    if params:
        identity = params.get("要求識別子")
        if not isinstance(identity, str) or identity not in series_ids:
            return None
        return identity if params.get("原稿") == path or identity in path_ids else None
    if path not in prompt:
        return None
    matches = {
        match[2].strip("`") for line in prompt.splitlines() if (match := _FIELD.fullmatch(line)) and match[1] == "要求識別子"
    }
    return next(iter(matches)) if len(matches) == 1 and matches <= path_ids else None


def period(collected: _Collected, since: datetime, until: datetime) -> dict[str, int | float | str]:
    counts = dict.fromkeys(("passed", "failed", "first_pass", "no_second", "missing_path", "unassigned"), 0)
    path_identities: dict[tuple[str, str], set[str]] = defaultdict(set)
    series_ids: dict[str, set[str]] = defaultdict(set)
    observations: list[tuple[_Receipt, list[tuple[str, str | None, str]], bool]] = []
    for receipt in collected.receipts:
        if collected.roles.get(receipt.session) != _DRAFT_ROLE:
            continue
        series = collected.series[receipt.session]
        rows = []
        judged = 0
        for fields in receipt.judgments:
            verdict = fields.get("判定")
            if fields.get("確認段階") != "本文" or verdict not in {"合格", "修正要"}:
                continue
            judged += 1
            path = fields.get("対象資料", "")
            if not path or path == "なし":
                counts["missing_path"] += int(since <= receipt.timestamp <= until)
                continue
            identity = fields.get("要求識別子")
            rows.append((path, identity, verdict))
            if identity:
                path_identities[series, path].add(identity)
                series_ids[series].add(identity)
        if judged:
            observations.append((receipt, rows, judged == 1))
    tracks = defaultdict(list)
    previous_single_failure: dict[str, tuple[datetime, str]] = {}
    for receipt, rows, single in observations:
        series = collected.series[receipt.session]
        requested = _latest_request(collected, receipt)
        prompt = (requested.prompt or "") if requested is not None else ""
        params = _request_fields(prompt)
        named_rejudgment = params.get("instruction") == "再判定せよ"
        previous = previous_single_failure.pop(receipt.session, None)
        candidates = [
            identity or _request_identity(prompt, params, path, path_identities[series, path], series_ids[series])
            for path, identity, _ in rows
        ]
        claims = Counter(candidates)
        for (path, identity, verdict), candidate in zip(rows, candidates, strict=True):
            path_ids = path_identities[series, path]
            first_path = path
            if not identity:
                if candidate is not None and claims[candidate] > 1:
                    counts["unassigned"] += int(since <= receipt.timestamp <= until)
                    continue
                identity = candidate
                selected = params.get("要求識別子")
                draft = params.get("原稿")
                if identity is None and named_rejudgment:
                    if (
                        isinstance(selected, str)
                        and isinstance(draft, str)
                        and single
                        and not path_ids
                        and (series, selected, None) in tracks
                    ):
                        identity = selected
                    elif (
                        selected is None
                        and draft == path
                        and single
                        and not path_ids
                        and previous is not None
                        and requested is not None
                        and previous[0] < requested.timestamp
                    ):
                        first_path = previous[1]
                    elif selected is not None or (not path_ids and (series, None, path) not in tracks):
                        counts["unassigned"] += int(since <= receipt.timestamp <= until)
                        continue
                if identity is None:
                    if len(path_ids) > 1:
                        counts["unassigned"] += int(since <= receipt.timestamp <= until)
                        continue
                    identity = next(iter(path_ids), None)
            key = (series, identity, None if identity else first_path)
            tracks[key].append((receipt.timestamp, receipt.order, verdict))
            if single and not identity and verdict == "修正要":
                previous_single_failure[receipt.session] = (receipt.timestamp, first_path)
    for track in tracks.values():
        track.sort()
        first, _, verdict = track[0]
        if not since <= first <= until:
            continue
        if verdict == "合格":
            counts["first_pass"] += 1
        elif len(track) < 2:
            counts["no_second"] += 1
        else:
            counts["passed" if track[1][2] == "合格" else "failed"] += 1
    denominator = counts["passed"] + counts["failed"]
    return {**counts, "pass_rate": counts["passed"] / denominator if denominator else "判定不能"}
