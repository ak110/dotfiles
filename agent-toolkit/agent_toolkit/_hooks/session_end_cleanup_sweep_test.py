"""SessionEndからのagents_server共有状態掃引を検証する。"""

from __future__ import annotations

import json

from agent_toolkit._agents_server import shared_sweep
from agent_toolkit._hooks import session_end_cleanup


def test_session_end_continues_when_shared_state_sweep_fails(monkeypatch) -> None:
    """SessionEndは掃引APIのOSErrorを通知して成功扱いで終了する。"""
    monkeypatch.setattr(
        shared_sweep,
        "sweep_stale_shared_state",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("x")),
    )
    monkeypatch.setattr(session_end_cleanup, "sweep_stale_states", lambda **_kwargs: None)

    result = session_end_cleanup.main(json.dumps({"hook_event_name": "SessionEnd", "session_id": "current"}))

    assert result == 0
