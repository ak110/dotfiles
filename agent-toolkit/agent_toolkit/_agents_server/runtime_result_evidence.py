from __future__ import annotations

import datetime
import json

from agent_toolkit._agents_server import record_paths
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import session_record_format

MIN_EXCERPT_LENGTH = 24


def _timestamp(value: object) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _current(value: object, started: datetime.datetime) -> bool:
    observed = _timestamp(value)
    return observed is not None and observed >= started


def _quoted_excerpt(reason: str, output: str) -> bool:
    reason = " ".join(reason.split())
    output = " ".join(output.split())
    return any(reason[index : index + MIN_EXCERPT_LENGTH] in output for index in range(len(reason) - MIN_EXCERPT_LENGTH + 1))


def has_runtime_evidence(session: SessionState, reason: str) -> bool:
    """当turn末の実障害か、再試行されていないツール結果の逐語引用があるかを返す。"""
    started = _timestamp(session.started_at)
    if started is None:
        return False
    if session.api_error and _current(session.api_error.get("first_at"), started):
        return True
    if (
        session.usage_limit is not None
        and session.usage_limit.is_wait_target
        and _current(session.usage_limit_first_at, started)
    ):
        return True
    try:
        record = record_paths.find_session_record(session.session_id)
        if record is None or len(record.paths) != 1:
            return False
        events = session_record_format.record_events(record.engine, session_record_format.parsed_records(record.paths[0]))
    except OSError, UnicodeError, ValueError:
        return False
    calls: dict[str, tuple[str, int]] = {}
    latest: dict[str, int] = {}
    results: list[tuple[str, str]] = []
    for index, event in enumerate(events):
        if not _current(event.timestamp, started) or not event.call_id:
            continue
        if event.kind == "tool_call" and event.name:
            signature = json.dumps([event.name, (event.detail or {}).get("input")], sort_keys=True, ensure_ascii=False)
            calls[event.call_id] = (signature, index)
            latest[signature] = index
        elif event.kind == "tool_result" and event.text:
            results.append((event.call_id, event.text))
    for call_id, output in results:
        call = calls.get(call_id)
        if call is not None and latest[call[0]] == call[1] and _quoted_excerpt(reason, output):
            return True
    return False
