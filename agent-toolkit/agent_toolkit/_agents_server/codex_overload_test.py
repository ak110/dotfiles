"""Codexのoverloadを検証する。"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import resume_waits, state
from agent_toolkit._testing.codex_backend_support import _completed_turn, _InspectableAppServerManager, _overloaded_turn

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.asyncio
async def test_overloaded_turn_after_availability_check_holds_result_and_reports_wait(tmp_path: pathlib.Path) -> None:
    """可用性確認を過ぎた後の過負荷は失敗を公開せず保留し、待機中の状態を`api_error`で示す。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_overloaded_turn(session))

        assert session.result_available is False
        assert session.awaiting_auto_resume is True
        assert session.status == "running"
        assert session.pending_result is not None
        assert session.pending_result["status"] == "failed"
        assert session.overload_resume_count == 1
        assert session.overload_resume_at is not None
        assert session.api_error is not None
        assert session.api_error["type"] == "serverOverloaded"
        assert session.api_error["http_status"] is None
        assert session.api_error["count"] == 1
        await manager.close()


@pytest.mark.asyncio
async def test_overloaded_reply_turn_is_held_even_before_flag(tmp_path: pathlib.Path) -> None:
    """委譲元の`send_message`で始めたturn（2番目以降）の過負荷も自動継続の対象とする。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-2", turn_seq=2)
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_overloaded_turn(session))

        assert session.awaiting_auto_resume is True
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("availability_checked", "error_info", "resume_count"),
    [
        (False, "serverOverloaded", 0),
        (True, "usageLimitExceeded", 0),
        (True, "rateLimitExceeded", 0),
        (True, "serverOverloaded", len(resume_waits.OVERLOAD_RESUME_DELAYS_SECONDS)),
    ],
)
async def test_turn_outside_overload_resume_is_published_as_failed(
    tmp_path: pathlib.Path, availability_checked: bool, error_info: str, resume_count: int
) -> None:
    """起動確認前の過負荷、他の可用性失敗、上限到達後の過負荷は現行どおり失敗として公開する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = availability_checked
    session.overload_resume_count = resume_count
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_overloaded_turn(session, error_info))

        assert session.result_available is True
        assert session.status == "failed"
        assert session.error["codexErrorInfo"] == error_info
        assert session.awaiting_auto_resume is False
        assert session.overload_resume_count == 0
        await manager.close()


@pytest.mark.asyncio
async def test_non_overload_completion_ends_overload_chain(tmp_path: pathlib.Path) -> None:
    """継続したturnが過負荷以外で終われば結果を公開し、連鎖の回数を戻す。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-2", turn_seq=2)
    session.overload_resume_count = 2
    session.overload_first_at = "2026-10-04T00:00:00+00:00"
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_completed_turn(session))

        assert session.result_available is True
        assert session.status == "completed"
        assert session.overload_resume_count == 0
        assert session.overload_first_at is None
        await manager.close()


@pytest.mark.asyncio
async def test_kill_during_overload_wait_publishes_last_failure_without_new_turn(tmp_path: pathlib.Path) -> None:
    """待機中の`kill`は新しいturnを始めず、保留した最後の失敗でsessionを終了する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_overloaded_turn(session))

        await manager.interrupt(session)

        assert session.result_available is True
        assert session.status == "failed"
        assert session.error["codexErrorInfo"] == "serverOverloaded"
        assert session.overload_resume_count == 0
        assert session.overload_resume_at is None
        assert session.turn_seq == 1
        await manager.close()


@pytest.mark.asyncio
async def test_send_message_during_overload_wait_delivers_instruction_as_reply(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """待機中の委譲元の`send_message`は保留を確定し、その指示を同じsessionのreplyとして配送する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    async with _InspectableAppServerManager({session.session_id: session}) as manager:
        await manager.handle_notification(_overloaded_turn(session))
        delivered: list[str] = []

        async def start_reply(_session: state.SessionState, prompt: str) -> tuple[str, dict[str, Any], None]:
            delivered.append(prompt)
            return "reply_started", {}, None

        monkeypatch.setattr(manager, "_start_reply_locked", start_reply)

        result = await manager.send_message(session, "委譲元の指示")

        assert delivered == ["委譲元の指示"]
        assert result["delivery"] == "reply_started"
        assert result["previous_result"]["status"] == "failed"
        assert session.awaiting_auto_resume is False
        assert session.overload_resume_count == 0
        assert session.overload_resume_at is None
        await manager.close()
