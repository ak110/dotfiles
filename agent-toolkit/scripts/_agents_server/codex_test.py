"""Codex backendのsession状態遷移を検証する。"""

import asyncio
import pathlib
from typing import Any

import pytest

from _agents_server import codex as subject
from _agents_server import state as shared_state


class _InspectableAppServerManager(subject.AppServerManager):
    """通知入力と管理対象taskの完了待機を検体へ公開する。"""

    async def handle_notification(self, message: dict[str, Any]) -> None:
        await self._handle_notification(message)

    async def wait_for_background_tasks(self) -> None:
        await asyncio.gather(*self._background_tasks)


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
async def test_pending_result_becomes_terminal_when_last_child_is_observed(tmp_path: pathlib.Path) -> None:
    """子session観測後にCodexの保留結果を公開可能な終端状態へ戻す。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))
    assert session.awaiting_auto_resume is True
    assert session.status == "running"

    await manager.handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "item": {
                    "type": "mcpToolCall",
                    "server": "agents_server",
                    "tool": "wait",
                    "arguments": {"session_id": "child-1"},
                    "result": {"structuredContent": {"status": "completed"}},
                },
            },
        }
    )

    assert session.result_available is True
    assert session.status == "completed"
    assert session.awaiting_auto_resume is False
    assert session.live_child_session_ids == set()
    await manager.close()


@pytest.mark.asyncio
async def test_pending_result_becomes_terminal_at_auto_resume_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """子sessionが未観測でも期限到来時にCodexの保留結果を終端状態へ戻す。"""
    monkeypatch.setattr(shared_state, "AUTO_RESUME_DEADLINE_SECONDS", 0.0)
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))
    await manager.wait_for_background_tasks()

    assert session.result_available is True
    assert session.status == "completed"
    assert session.error == {"unobservedSessions": ["child-1"]}
    assert session.live_child_session_ids == set()
    await manager.close()
