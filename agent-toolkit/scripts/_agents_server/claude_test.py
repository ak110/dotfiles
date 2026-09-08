"""Claude backendと共有する自動再開状態の契約を検証する。"""

import asyncio

import pytest

from _agents_server import state as shared_state


@pytest.mark.asyncio
async def test_auto_resume_deadline_is_independent_from_result_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """自動再開の待機上限を終端結果の保持期限から独立して算出する。"""
    monkeypatch.setattr(shared_state, "AUTO_RESUME_DEADLINE_SECONDS", 5.0)
    monkeypatch.setattr(shared_state, "RESULT_RETENTION_SECONDS", 0.0)
    session = shared_state.SessionState("claude-session", "/tmp", engine="claude")
    before = asyncio.get_running_loop().time()

    deadline = shared_state.begin_auto_resume_wait(
        session,
        {"status": "completed", "agent_message": "完了", "error": None},
    )

    assert before + 5.0 <= deadline <= asyncio.get_running_loop().time() + 5.0
    assert session.auto_resume_deadline == deadline
    assert session.awaiting_auto_resume is True
