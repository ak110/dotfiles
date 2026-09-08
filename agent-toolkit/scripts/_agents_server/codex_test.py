"""Codex backendのsession状態遷移を検証する。"""

import pathlib
from typing import Any

import pytest

from _agents_server import codex as subject
from _agents_server import state as shared_state


class _InspectableAppServerManager(subject.AppServerManager):
    """通知入力を検体へ公開する。"""

    async def handle_notification(self, message: dict[str, Any]) -> None:
        await self._handle_notification(message)


def _completed_turn(session: shared_state.SessionState) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": session.session_id,
            "turn": {
                "id": session.turn_id,
                "status": "completed",
                "error": None,
            },
        },
    }


@pytest.mark.asyncio
async def test_completed_turn_with_unobserved_child_is_published_immediately(tmp_path: pathlib.Path) -> None:
    """未観測の子sessionを記録し、Codexのturn終端結果を直ちに公開する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is True
    assert session.status == "completed"
    assert session.awaiting_auto_resume is False
    assert session.error == {"unobservedSessions": ["child-1"]}
    assert session.live_child_session_ids == set()
    await manager.close()
