"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import pathlib

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import result_projection, state
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support import (
    BlockingContinuationClaudeClient,
    FailedResumeBackend,
    _actionable_message,
    _assert_no_forbidden_keys,
    _complete,
    _manager_with_fake,
    _without_root,
    claude_manager,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_send_message_continues_when_selected_candidate_remains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補の順序が変わっても採用済み候補が残るsessionを継続する。"""
    current = [("codex", "first", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: current)
    async with _manager_with_fake("codex") as (manager, backend):
        response = await manager.start("plan", "調査", str(tmp_path))
        session_id = response["session_id"]
        current[:] = [("codex", "replacement", "high"), ("codex", "first", "high")]

        result = await manager.send_message(session_id, "続行")

        assert result["delivery"] == "steered"
        assert session_id in manager.sessions
        assert backend.send_calls == 1


@pytest.mark.asyncio
async def test_send_message_continues_without_model_type(tmp_path: pathlib.Path) -> None:
    """候補列を直接指定したsessionは工程別モデル設定を比較せず継続する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = await backend.start("調査", str(tmp_path), "direct", "high")

        result = await manager.send_message(session.session_id, "続行")

        assert result["delivery"] == "steered"
        assert backend.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("updated", [[("claude", "replacement", "medium")], []])
async def test_send_message_continues_after_candidates_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    updated: list[tuple[str, str, str]],
) -> None:
    """起動後に候補列が別engineへ置換された場合と、候補が残らない場合のいずれでもsessionを継続する。"""
    current = [("codex", "first", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: current)
    async with _manager_with_fake("codex") as (manager, backend):
        response = await manager.start("plan", "調査", str(tmp_path))
        session_id = response["session_id"]
        current[:] = updated

        result = await manager.send_message(session_id, "続行")

        assert result["delivery"] == "steered"
        assert session_id in manager.sessions
        assert backend.send_calls == 1


@pytest.mark.asyncio
async def test_send_message_rejects_active_interrupt_without_backend_call(tmp_path: pathlib.Path) -> None:
    """中断要求が有効な未終了turnへ継続入力を送らない。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        session.interrupt_requested = True
        manager.sessions[session.session_id] = session

        with pytest.raises(ValueError, match="session is being interrupted: thread-1") as raised:
            await manager.send_message(session.session_id, "追加指示")

        assert "atk agents wait" in _actionable_message(raised.value)

        assert backend.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize("delivery", ["reply_started", "reply_failed", "reply_ambiguous"])
async def test_send_message_terminal_session_returns_previous_result_without_internal_fields(
    engine: str,
    delivery: str,
    tmp_path: pathlib.Path,
) -> None:
    """終了後の継続入力は回収済みフラグを要求せず、直前本文を退避する。"""
    async with _manager_with_fake(engine, delivery) as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine=engine)
        _complete(session, message="直前の結果")
        manager.sessions[session.session_id] = session
        response = await manager.send_message(session.session_id, "続行")
        assert response["delivery"] == delivery
        assert response["previous_result"] == {
            "status": "completed",
            "agent_message": "直前の結果",
        }
        _assert_no_forbidden_keys(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize(
    ("label", "expected"),
    [("pr-body-review", "reviewee.md"), ("lane-01-reader-fit-review", "reader-fit-review.parent.md"), ("explore-pyfltr", None)],
)
async def test_send_message_previous_result_guides_review_findings(
    engine: str, label: str, expected: str | None, tmp_path: pathlib.Path
) -> None:
    """レビューを目的とするsessionの未回収の完了結果を`previous_result`で返す場合も、採否確定の次の操作を加える。"""
    async with _manager_with_fake(engine, "reply_started") as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine=engine, label=label)
        _complete(session, message="指摘1件")
        manager.sessions[session.session_id] = session

        response = await manager.send_message(session.session_id, "再レビュー")

        previous = response["previous_result"]
        if expected is None:
            assert "next_action" not in previous
        else:
            assert expected in previous["next_action"]
            if expected == "reader-fit-review.parent.md":
                assert "reviewee.md" not in previous["next_action"]
            else:
                assert previous["next_action"] == result_projection.REVIEW_RESULT_NEXT_ACTION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "expected"),
    [("lane-01-exec-review", "reviewee.md"), ("lane-01-reader-fit-review", "reader-fit-review.parent.md")],
)
async def test_stopped_review_session_previous_result_guides_review_findings(
    label: str, expected: str, tmp_path: pathlib.Path
) -> None:
    """破棄済みのレビューsessionの未回収結果を再開時に返す場合も、同じ次の操作を加える。"""
    resume_state = state.SessionResumeState(
        session_id="stopped-review",
        cwd=str(tmp_path),
        model="model",
        effort="medium",
        engine="codex",
        status="completed",
        agent_message="指摘1件",
        finalized_at="2026-10-01T00:00:00+00:00",
        label=label,
    )

    response = server_manager.AgentsServerManager._stopped_result_response(resume_state)

    assert response is not None
    assert expected in response["next_action"]
    if expected == "reader-fit-review.parent.md":
        assert "reviewee.md" not in response["next_action"]
    else:
        assert response["next_action"] == result_projection.REVIEW_RESULT_NEXT_ACTION


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize("label", ["add-wi", "lane-01-exec-review", "lane-01-reader-fit-review"])
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_send_message_previous_and_stopped_results_relay_improvements(
    engine: str, label: str, status: str, tmp_path: pathlib.Path
) -> None:
    """継続入力と破棄済みsessionの未回収結果が、同じ改善点の案内と本文を返す。"""
    async with _manager_with_fake(engine, "reply_started") as (manager, _):
        message = "完了\n  気付いた改善点: 再読した\n気付いた改善点: 反復した"
        session = state.SessionState("thread-1", str(tmp_path), engine=engine, label=label)
        _complete(session, message=message)
        session.status = status
        manager.sessions[session.session_id] = session
        stopped = state.SessionResumeState.from_session(session)

        response = await manager.send_message(session.session_id, "続行")
        previous = response["previous_result"]
        stopped_result = server_manager.AgentsServerManager._stopped_result_response(stopped)

        assert previous == stopped_result
        assert previous["agent_message"] == message
        action = previous["next_action"]
        assert action.count(result_projection.IMPROVEMENT_RESULT_NEXT_ACTION) == 1
        assert "メインエージェントは次のユーザーへの発話へ" in action
        assert "委譲先は受領時点で上流へ逐語で中継し、完了返却の末尾にも残す" in action
        assert "字下げを除く行頭に`気付いた改善点:`を原文のまま置き" in action
        assert "標識とコロンの間へ報告元などの語を入れない" in action
        assert "標識行の原文の後ろか別の行に添える" in action
        assert "`agent-toolkit:delegation`の`references/receiving.md`「受領後の扱い」" in action
        assert (result_projection.REVIEW_RESULT_NEXT_ACTION in action) is (
            status == "completed" and label == "lane-01-exec-review"
        )
        assert (result_projection.READER_FIT_REVIEW_RESULT_NEXT_ACTION in action) is (
            status == "completed" and label == "lane-01-reader-fit-review"
        )


@pytest.mark.asyncio
async def test_send_message_omits_previous_result_after_wait_returned_result(tmp_path: pathlib.Path) -> None:
    """waitで回収した結果本文を継続入力の応答へ再送しない。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        _complete(session, message="回収済み結果")
        manager.sessions[session.session_id] = session

        assert (await manager.wait())["agent_message"] == "回収済み結果"
        response = await manager.send_message(session.session_id, "続行")

        assert "previous_result" not in response


@pytest.mark.asyncio
async def test_send_message_omits_previous_result_after_kill_returned_result(tmp_path: pathlib.Path) -> None:
    """killで回収した結果本文を継続入力の応答へ再送しない。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        _complete(session, message="回収済み結果")
        manager.sessions[session.session_id] = session

        assert (await manager.kill(session.session_id, timeout=0))["agent_message"] == "回収済み結果"
        response = await manager.send_message(session.session_id, "続行")

        assert "previous_result" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_send_message_keeps_previous_result_for_unwaited_second_turn(
    engine: str,
    tmp_path: pathlib.Path,
) -> None:
    """前turnの回収状態を次turnへ持ち越さず、未回収結果を返す。"""
    async with _manager_with_fake(engine) as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine=engine)
        _complete(session, message="結果A")
        manager.sessions[session.session_id] = session

        await manager.wait()
        first = await manager.send_message(session.session_id, "1回目")
        assert "previous_result" not in first

        _complete(session, message="結果B")
        second = await manager.send_message(session.session_id, "2回目")
        assert second["previous_result"]["agent_message"] == "結果B"


@pytest.mark.asyncio
async def test_send_message_keeps_previous_result_after_wait_without_result(tmp_path: pathlib.Path) -> None:
    """結果本文を含まないwaitは後続の終了時の結果を回収済みにしない。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        manager.sessions[session.session_id] = session

        assert "agent_message" not in await manager.wait()
        _complete(session, message="未回収結果")
        response = await manager.send_message(session.session_id, "続行")

        assert response["previous_result"]["agent_message"] == "未回収結果"


@pytest.mark.asyncio
async def test_send_message_keeps_previous_result_after_started_turn_fails(tmp_path: pathlib.Path) -> None:
    """配送後に実行失敗したturnの結果を、次の追送のprevious_resultから回収できる。"""
    async with server_manager.AgentsServerManager() as manager:
        backend = FailedResumeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        session_id = "thread-1"
        manager.expired_sessions[session_id] = state.SessionResumeState(
            session_id=session_id,
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="codex",
        )

        first = await manager.send_message(session_id, "1回目")
        assert _without_root(first) == {"delivery": "reply_started", "label": ""}

        second = await manager.send_message(session_id, "2回目")
        assert second["previous_result"]["agent_message"] == "reply失敗結果"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize(
    ("delivery", "expected_progress"),
    [("reply_started", ""), ("reply_ambiguous", ""), ("reply_failed", "直前の進捗")],
)
async def test_reply_resets_progress_only_after_delivery_is_accepted(
    engine: str,
    delivery: str,
    expected_progress: str,
    tmp_path: pathlib.Path,
) -> None:
    """reply失敗時は直前の進捗を保持し、replyが開始済みか開始の成否が曖昧な場合だけ破棄する。"""
    async with _manager_with_fake(engine, delivery) as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine=engine)
        session.set_progress("直前の進捗")
        _complete(session)
        manager.sessions[session.session_id] = session

        response = await manager.send_message(session.session_id, "続行")

        assert response["delivery"] == delivery
        assert session.progress == expected_progress


@pytest.mark.asyncio
async def test_send_message_steered_response_has_no_previous_result(tmp_path: pathlib.Path) -> None:
    """実行中turnへの追加指示はsteeredとして本文を退避しない。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        session.turn_id = "turn-1"
        manager.sessions[session.session_id] = session
        response = await manager.send_message(session.session_id, "追加指示")
        assert _without_root(response) == {"delivery": "steered", "label": ""}


@pytest.mark.asyncio
async def test_send_message_timeout_reports_undetermined_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続要求の配送結果が確定しない場合に指定上限で打ち切る。"""
    client = BlockingContinuationClaudeClient()
    async with claude_manager(monkeypatch, client) as (manager, backend):
        session = await backend.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)

        with pytest.raises(TimeoutError, match="send_message timed out: claude-blocking; delivery is undetermined") as raised:
            await manager.send_message(session.session_id, "継続", timeout=0.01)
        assert "atk agents wait" in _actionable_message(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_send_message_includes_stopped_previous_result_regardless_of_deadline(
    expired: bool,
    tmp_path: pathlib.Path,
) -> None:
    """stop=true後の再開は未回収であれば期限後も直前結果を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState(f"stopped-send-{expired}", str(tmp_path), engine="codex")
        _complete(session, message="直前結果")
        manager.sessions[session.session_id] = session
        await manager.kill(session.session_id, timeout=0, stop=True)
        if expired:
            manager.stopped_sessions[session.session_id] = dataclasses.replace(
                manager.stopped_sessions[session.session_id],
                retention_deadline=asyncio.get_running_loop().time() - 1,
            )

        response = await manager.send_message(session.session_id, "再開")

        assert response["previous_result"]["agent_message"] == "直前結果"
