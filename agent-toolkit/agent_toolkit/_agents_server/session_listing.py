from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    context_usage,
    result_projection,
    retained_results,
    shared_layout,
    status_reader,
    turn_state,
)
from agent_toolkit._common import session_activity, session_registry

_RIGHT_SEPARATOR = " · "


def list_groups(
    *, human: bool, root_session_id: str | None, include_terminated: bool
) -> list[tuple[str, list[dict[str, Any]]]]:
    living_roots = session_activity.living_root_session_ids() if human else frozenset()
    root_ids = (
        sorted(set(shared_layout.list_root_session_ids()) | living_roots)
        if human
        else ([root_session_id] if root_session_id else [])
    )
    groups = [(root_id, load_sessions(root_id)) for root_id in root_ids]
    if not include_terminated:
        groups = [
            (root_id, [s for s in sessions if s.get("status") == "running" or s.get("result_available") is True])
            for root_id, sessions in groups
        ]
    return [(root_id, sessions) for root_id, sessions in groups if sessions or root_id in living_roots]


def tree_rows(
    groups: list[tuple[str, list[dict[str, Any]]]],
) -> list[str | tuple[str, dict[str, Any]]]:
    rows: list[str | tuple[str, dict[str, Any]]] = []
    for root_id, sessions in groups:
        rows.append(f"root {root_id}")
        for branches, session in _tree_sessions(sessions):
            prefix = "".join("   " if last else "│  " for last in branches[:-1])
            marker = "└─ " if branches[-1] else "├─ "
            rows.append((f"{prefix}{marker}{session['session_id']}", session))
    return rows


def _tree_sessions(sessions: list[dict[str, Any]]) -> list[tuple[tuple[bool, ...], dict[str, Any]]]:
    children: dict[str | None, list[dict[str, Any]]] = {}
    attached: set[str] = set()
    rows: list[tuple[tuple[bool, ...], dict[str, Any]]] = []
    for session in sessions:
        children.setdefault(session.get("host_session_id"), []).append(session)
    _append_children(children, None, (), attached, rows)
    identifiers = {session["session_id"] for session in sessions}
    for owner in children:
        if owner is not None and owner not in identifiers:
            _append_children(children, owner, (), attached, rows)
    for session in sessions:
        if session["session_id"] not in attached:
            _append_children({**children, None: [session]}, None, (), attached, rows)
    return rows


def serve_agent_listing(*, include_terminated: bool) -> list[dict[str, Any]]:
    groups = list_groups(human=True, root_session_id=None, include_terminated=include_terminated)
    now = datetime.datetime.now(datetime.UTC)
    roots: list[dict[str, Any]] = []
    for root_id, sessions in groups:
        children = []
        for branches, session in _tree_sessions(sessions):
            children.append(
                {
                    "id": session["session_id"],
                    "engine": session.get("engine"),
                    "depth": len(branches),
                    "name": session_name(session),
                    "running": session.get("status") == "running",
                    **dataclasses.asdict(session_status(session, now)),
                }
            )
        root = session_activity.root_activity(root_id)
        info = session_registry.resolve(root_id).resume_info
        if info is not None:
            root["engine"] = info.engine
            root["name"] = session_name(
                {
                    "engine": info.engine,
                    "model": info.model,
                    "effort": info.effort,
                    "launch_kind": info.launch_kind,
                    "fast_mode": info.fast_mode,
                }
            )
            root["elapsed"] = _elapsed(info.started_at, now)
        roots.append({"id": root_id, **root, "children": children, "root": True})
    return roots


def _append_children(
    children: dict[str | None, list[dict[str, Any]]],
    owner: str | None,
    branches: tuple[bool, ...],
    attached: set[str],
    rows: list[tuple[tuple[bool, ...], dict[str, Any]]],
) -> None:
    entries = children.get(owner, [])
    for index, session in enumerate(entries):
        session_id = str(session["session_id"])
        if session_id in attached:
            continue
        attached.add(session_id)
        path = (*branches, index == len(entries) - 1)
        rows.append((path, session))
        _append_children(children, session_id, path, attached, rows)


def session_name(session: Mapping[str, Any]) -> str:
    label = session.get("label") or session.get("launch_kind") or "session"
    engine = session.get("engine") or ""
    model = session.get("model") or ""
    effort = session.get("effort") or ""
    speed = "@fast" if engine == "codex" and session.get("fast_mode") is True else ""
    if model:
        detail = f"{engine}:{model}" if engine else str(model)
        if effort:
            detail += f"/{effort}"
        detail += speed
    elif engine:
        detail = f"{engine}{speed}"
    else:
        detail = str(session.get("model_type") or "")
    return f"{label} ({detail})" if detail else str(label)


@dataclasses.dataclass(frozen=True)
class SessionStatus:
    action: str
    status: str
    elapsed: str | None
    usage: str | None
    retry_detail: str | None = None
    retry_count: str | None = None


def session_status(session: Mapping[str, Any], now: datetime.datetime) -> SessionStatus:
    """利用上限の解除待ちまたはAPI再試行を通常の進捗より優先する。 pyfltr: allow-comment"""
    status = str(session.get("status") or "")
    raw_error = session.get("api_error") if status == "running" else None
    api_error = raw_error if isinstance(raw_error, dict) and raw_error.get("type") else None
    tokens = context_usage.format_tokens(session.get("context_tokens"), session.get("context_window"))
    retry_detail = None
    retry_count = None
    elapsed = _elapsed(session.get("started_at"), now)
    if api_error is not None and api_error.get("type") == turn_state.USAGE_LIMIT_ERROR_TYPE:
        description = f"利用上限の解除待ち {api_error.get('limit_type') or '?'}"
        remaining = _elapsed(now, api_error.get("resets_at"))
        retry_detail = f"解除まで{remaining}" if remaining is not None else "解除時刻確認中"
        elapsed = _elapsed(api_error.get("first_at"), now)
    elif api_error is not None:
        description = f"API再試行 {api_error['type']}"
        http_status = api_error.get("http_status")
        retry_detail = f"HTTP {http_status}" if http_status else ""
        elapsed = _elapsed(api_error.get("first_at"), now)
        if api_error.get("count"):
            retry_count = f"{api_error['count']}回"
    else:
        description = str(session.get("last_action") or session.get("progress") or "")
    return SessionStatus(" ".join(description.split()), status, elapsed, tokens, retry_detail, retry_count)


def status_parts(session: Mapping[str, Any], now: datetime.datetime) -> tuple[str, str]:
    fields = session_status(session, now)
    if fields.retry_detail is not None:
        right = (fields.retry_detail or "HTTP ?", fields.elapsed or "?", fields.retry_count, fields.usage)
    else:
        right = (fields.elapsed, fields.usage, fields.status)
    return fields.action, _RIGHT_SEPARATOR.join(part for part in right if part)


def _elapsed(start: object, end: object) -> str | None:
    moments: list[datetime.datetime] = []
    for value in (start, end):
        if isinstance(value, datetime.datetime):
            moment = value
        elif isinstance(value, str):
            try:
                moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        moments.append(moment if moment.tzinfo is not None else moment.replace(tzinfo=datetime.UTC))
    seconds = int((moments[1] - moments[0]).total_seconds())
    if seconds < 0:
        return None
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60}s"
    return f"{seconds}s"


def load_sessions(root_session_id: str) -> list[dict[str, Any]]:
    by_id = status_reader.load_root(root_session_id).sessions
    for session in by_id.values():
        session["result_available"] = (
            retained_results.read_retained_result(
                root_session_id,
                session["session_id"],
                expected_turn_seq=session.get("turn_seq"),
                expected_finalized_at=session.get("finalized_at"),
            )
            is not None
        )
        add_output_activity(session)
    return sorted(by_id.values(), key=lambda session: str(session.get("started_at", "")))


def add_output_activity(session: dict[str, Any]) -> None:
    updated_at = session.get("updated_at")
    output_updated_at = session.get("output_updated_at")
    started_at = session.get("started_at")
    activity = result_projection.activity_projection(
        updated_at=updated_at if isinstance(updated_at, str) else None,
        output_updated_at=output_updated_at if isinstance(output_updated_at, str) else None,
        started_at=started_at if isinstance(started_at, str) else None,
        api_error=session.get("api_error") if isinstance(session.get("api_error"), dict) else None,
    )
    session.update(activity)
