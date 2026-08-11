"""SubagentStart hook: 子委譲を持つ調整役を親セッション状態へ登録する。"""

from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _tracked_subagent_types import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    TRACKED_SUBAGENT_TYPES,
)

_PLAN_IMPL_EXECUTOR_ACTIVE_KEY = "plan_impl_executor_active_subagent_sessions"


def main(payload_text: str) -> int:
    """対象の委譲調整役を登録する。入力不備と対象外はfail-openで通過させる。"""
    try:
        payload = json.loads(payload_text or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SubagentStart":
        return 0
    session_id = payload.get("session_id")
    agent_id = payload.get("agent_id")
    agent_type = payload.get("agent_type")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(agent_id, str)
        or not agent_id
        or not isinstance(agent_type, str)
        or agent_type not in TRACKED_SUBAGENT_TYPES
    ):
        return 0

    def _register(state: dict) -> dict | None:
        active = state.get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
        if not isinstance(active, dict):
            active = {}
        current = active.get(agent_id)
        if isinstance(current, dict) and current.get("subagent_type") == agent_type:
            return None
        active[agent_id] = {"subagent_type": agent_type, "started_at": time.time()}
        state[_PLAN_IMPL_EXECUTOR_ACTIVE_KEY] = active
        return state

    update_state(session_id, _register)
    return 0
