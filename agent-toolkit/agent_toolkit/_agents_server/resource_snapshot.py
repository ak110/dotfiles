"""sessionの稼働状態が変わった時点の共有状態とホスト資源を既存ログへ記録する。"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
from typing import Any

import psutil

from agent_toolkit._agents_server import shared_layout, state, status_reader

_LOG = logging.getLogger("agent-toolkit.agents-server.resources")


def record(
    event: str,
    session: state.SessionState,
    *,
    root_session_id: str,
    sessions: dict[str, state.SessionState],
    state_root: pathlib.Path | None = None,
) -> None:
    """計測の失敗をsession処理から隔離し、欠損とその理由もログへ残す。"""
    try:
        payload: dict[str, Any] = {
            "event": event,
            "time": datetime.datetime.now(datetime.UTC).isoformat(),
            "root_session_id": root_session_id,
            "session_id": session.session_id,
            "turn_seq": session.turn_seq,
            "scope": "observed_state_directory",
            **_session_counts(root_session_id, sessions, state_root),
        }
        errors: dict[str, str] = {}
        try:
            payload["available_memory_bytes"] = psutil.virtual_memory().available
        except Exception as exc:
            payload["available_memory_bytes"] = None
            errors["available_memory_bytes"] = type(exc).__name__
        if os.name == "nt":
            payload["load_average_1_5_15"] = None
            errors["load_average_1_5_15"] = "OSがロードアベレージを提供しない"
        else:
            try:
                payload["load_average_1_5_15"] = os.getloadavg()
            except (AttributeError, OSError) as exc:
                payload["load_average_1_5_15"] = None
                errors["load_average_1_5_15"] = type(exc).__name__
        payload["unavailable_fields"] = errors
        _LOG.info("resource_snapshot %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception:
        # 診断材料の採取が通常の結果配送を止めないよう、計測の境界で例外を隔離する。
        _LOG.warning(
            "resource_snapshot_failed event=%s root_session_id=%s session_id=%s turn_seq=%d; "
            "session処理は継続した。資源記録の取得失敗を確認する",
            event,
            root_session_id,
            session.session_id,
            session.turn_seq,
            exc_info=True,
        )


def _session_counts(
    own_root: str,
    sessions: dict[str, state.SessionState],
    state_root: pathlib.Path | None,
) -> dict[str, Any]:
    cutoff = status_reader.heartbeat_cutoff()
    failures: list[str] = []
    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    try:
        roots = shared_layout.list_root_session_ids(state_root, strict=True)
    except OSError as exc:
        roots = []
        failures.append(f"roots: {type(exc).__name__}")
    for root in roots:
        snapshot = status_reader.load_root(root, state_root, cutoff=cutoff)
        failures.extend(snapshot.failures)
        for session_id, entry in snapshot.sessions.items():
            previous = by_id.get(session_id)
            if previous is None or str(previous[1].get("updated_at", "")) <= str(entry.get("updated_at", "")):
                by_id[session_id] = (root, entry)
    # 集約書込の前に起きた遷移も反映する。自身が所有するsessionの現物は共有ファイルより新しい。
    for session in sessions.values():
        by_id[session.session_id] = (own_root, {"status": session.status})
    counts: dict[str, int] = dict.fromkeys([*roots, own_root], 0)
    for root, entry in by_id.values():
        status = entry.get("status")
        if status == "running":
            counts[root] = counts.get(root, 0) + 1
        elif status not in {"starting", "completed", "failed", "interrupted"}:
            failures.append(f"{root}: invalid session status")
    total = sum(counts.values())
    payload: dict[str, Any] = {
        "counts_complete": not failures,
        "running_total": total if not failures else None,
        "running_by_root": counts if not failures else None,
        "state_read_failures": failures,
    }
    if failures:
        payload.update(observed_running_total=total, observed_running_by_root=counts)
    return payload
