"""`_agents_server/manager_wait.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import logging
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    retained_results,
    shared_layout,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    _assert_no_forbidden_keys,
    _complete,
    _default_identity_fields,
    _manager_with_fake,
    _set_wait_timeout,
    _write_notice,
    _writer_backed_manager,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_wait_does_not_redeliver_only_terminal_result_at_timeout(tmp_path: pathlib.Path) -> None:
    """配送済み終端結果だけが残る上限応答は本文なしのrunningを返す。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="最終結果", error={"message": "補足"})
    manager.sessions[session.session_id] = session
    first = await manager.wait()
    second = await manager.wait()
    assert first == {
        "session_id": session.session_id,
        "status": "failed",
        "agent_message": "最終結果",
        "error": {"message": "補足"},
        **_default_identity_fields(),
    }
    assert second["session_id"] == session.session_id
    assert second["status"] == "running"
    assert second["progress"] == ""
    assert isinstance(second["elapsed_seconds"], int)
    assert "agent_message" not in second
    _assert_no_forbidden_keys(first)


@pytest.mark.asyncio
async def test_wait_returns_first_available_session_and_retains_others(tmp_path: pathlib.Path) -> None:
    """waitは確定時刻が早い結果だけを選び、残る結果を保持する。"""
    manager, _ = _manager_with_fake("codex")
    later = state.SessionState("later", str(tmp_path), engine="codex")
    earlier = state.SessionState("earlier", str(tmp_path), engine="codex")
    _complete(later, message="後")
    _complete(earlier, message="先")
    later.finalized_at = "2026-09-10T00:00:02+00:00"
    earlier.finalized_at = "2026-09-10T00:00:01+00:00"
    manager.sessions.update({later.session_id: later, earlier.session_id: earlier})

    response = await manager.wait()

    assert response["session_id"] == earlier.session_id
    assert response["agent_message"] == "先"
    assert later.result_delivered is False


@pytest.mark.asyncio
async def test_wait_delivers_each_terminal_result_to_single_waiter(tmp_path: pathlib.Path) -> None:
    """同時に待つ2件へ同じ終端結果を重複配送しない。"""
    manager, _ = _manager_with_fake("codex")
    sessions = [state.SessionState(f"thread-{index}", str(tmp_path), engine="codex") for index in range(2)]
    for session in sessions:
        _complete(session, message=session.session_id)
        manager.sessions[session.session_id] = session

    responses = await asyncio.gather(*(manager.wait() for _ in range(2)))

    assert {response["session_id"] for response in responses} == {session.session_id for session in sessions}


@pytest.mark.asyncio
async def test_wait_does_not_redeliver_selected_terminal_result(tmp_path: pathlib.Path) -> None:
    """配送済みの終端結果を避け、残る未終端sessionの状態を返す。"""
    manager, _ = _manager_with_fake("codex")
    completed = state.SessionState("thread-completed", str(tmp_path), engine="codex")
    running = state.SessionState("thread-running", str(tmp_path), engine="codex")
    _complete(completed, message="配送済み")
    manager.sessions.update({completed.session_id: completed, running.session_id: running})

    selected = await manager.wait()
    response = await manager.wait()

    assert selected["session_id"] == completed.session_id
    assert selected["agent_message"] == "配送済み"
    assert response["session_id"] == running.session_id
    assert response["status"] == "running"
    assert "agent_message" not in response


@pytest.mark.asyncio
async def test_wait_does_not_return_unfinished_result(tmp_path: pathlib.Path) -> None:
    """未終端sessionの待機上限応答は本文なしの現状態を返す。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session
    response = await manager.wait()
    assert response["session_id"] == session.session_id
    assert response["status"] == "running"
    assert response["progress"] == ""
    assert isinstance(response["elapsed_seconds"], int)
    assert response["elapsed_seconds"] >= 0


@pytest.mark.asyncio
async def test_wait_returns_running_notification_once(tmp_path: pathlib.Path) -> None:
    """waitは実行中の通知を検出して復帰し、同じ通知を再配送しない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    manager = server_manager.AgentsServerManager(writer)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session
    _set_wait_timeout(manager, 2.0)
    wait_task = asyncio.create_task(manager.wait())
    await asyncio.sleep(0)
    notices = shared_layout.notices_directory("root-session", tmp_path)
    _write_notice(notices, session.session_id, 2, "2026-09-06T00:00:02+00:00", "後の通知")
    _write_notice(notices, session.session_id, 1, "2026-09-06T00:00:01+00:00", "先の通知")

    response = await wait_task

    assert response["status"] == "running"
    assert "agent_message" not in response
    assert response["notices"] == [
        {"body": "先の通知"},
        {"body": "後の通知"},
    ]
    _set_wait_timeout(manager, 0.0)
    second = await manager.wait()
    assert "notices" not in second
    await manager.close()


@pytest.mark.asyncio
async def test_wait_returns_terminal_result_with_pending_notification(tmp_path: pathlib.Path) -> None:
    """終端時に残る通知を結果本文と同じ応答へ載せる。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    manager = server_manager.AgentsServerManager(writer)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="最終結果")
    manager.sessions[session.session_id] = session
    notices = shared_layout.notices_directory("root-session", tmp_path)
    _write_notice(notices, session.session_id, 1, "2026-09-06T00:00:01+00:00", "終端前の通知")

    response = await manager.wait()

    assert response["status"] == "completed"
    assert response["agent_message"] == "最終結果"
    assert response["notices"] == [{"body": "終端前の通知"}]
    assert not any(notices.iterdir())
    await manager.close()


@pytest.mark.asyncio
async def test_wait_derives_timeout_from_the_main_bucket_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """待機上限はmainのbucketから1度だけ導出し、以降の呼び出しへ再利用する。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session
    requested_buckets: list[str] = []

    def fake_get_wait_timeout(request_bucket: str, **kwargs: Any) -> float:
        del kwargs
        requested_buckets.append(request_bucket)
        return 0.0

    monkeypatch.setattr(_wait_schedule, "get_wait_timeout", fake_get_wait_timeout)

    assert (await manager.wait())["status"] == "running"
    assert (await manager.wait())["status"] == "running"

    assert requested_buckets == ["main"]


@pytest.mark.asyncio
async def test_agents_wait_ignores_previous_turn_result_until_next_turn_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """前turnの結果を保持したまま、指定した次turnの終端だけを待つ。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("codex", None, None)])
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    started = await manager.start("high_tier", "実装", str(tmp_path))
    session = manager.sessions[started["session_id"]]
    _complete(session, message="結果A")
    writer.flush()
    assert (await manager.wait())["status"] == "completed"
    assert session.turn_seq == 1

    continued = await manager.send_message(session.session_id, "続行")
    assert continued["delivery"] == "reply_started"
    assert session.turn_seq == 2
    writer.flush()
    wait_task = asyncio.create_task(
        asyncio.to_thread(
            agents_wait.wait_for_result,
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
    )
    await asyncio.sleep(0.02)
    assert not wait_task.done()

    _complete(session, message="結果B")
    writer.flush()
    assert await wait_task == 0
    output = json.loads(capsys.readouterr().out)
    assert output["agent_message"] == "結果B"
    assert session.turn_seq == 2
    assert {"turn_seq", "finalized_at"}.isdisjoint(output)
    await manager.close()


@pytest.mark.asyncio
async def test_wait_returns_current_registry_state_after_replacement(tmp_path: pathlib.Path) -> None:
    """wait開始後に登録簿が更新された場合は新しい終端状態を返す。"""
    manager, _ = _manager_with_fake("claude")
    original = state.SessionState("claude-replaced", str(tmp_path), engine="claude")
    manager.sessions[original.session_id] = original

    _set_wait_timeout(manager, 1.0)
    wait_task = asyncio.create_task(manager.wait())
    await asyncio.sleep(0)
    replacement = state.SessionState(original.session_id, str(tmp_path), engine="claude")
    _complete(replacement, message="差し替え後の結果")
    manager.sessions[original.session_id] = replacement
    await manager._notify_waiters()

    response = await wait_task

    assert response["status"] == "completed"
    assert response["agent_message"] == "差し替え後の結果"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_expired_session_wait_returns_uncollected_result(engine: str, tmp_path: pathlib.Path) -> None:
    """両engineで保持期限後も最初のwaitへ結果本文を返す。"""
    manager, _ = _manager_with_fake(engine)
    session = state.SessionState("expired", str(tmp_path), engine=engine)
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session
    response = await manager.wait()
    assert response == {"session_id": session.session_id, "status": "completed", "agent_message": "完了"}
    assert await manager.wait() == {"status": "expired"}
    assert "expired" not in manager.sessions
    assert manager.expired_sessions["expired"].result_delivered is True


@pytest.mark.asyncio
async def test_wait_returns_uncollected_result_from_expired_state(tmp_path: pathlib.Path) -> None:
    """退避済みの期限切れ状態から未回収の結果本文を返す。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("expired", str(tmp_path))
    _complete(session, message="退避結果")
    manager.expired_sessions[session.session_id] = state.SessionResumeState.from_session(session)

    assert manager.show_session(session.session_id)["result_available"] is True
    response = await manager.wait()
    assert response == {"session_id": session.session_id, "status": "completed", "agent_message": "退避結果"}
    assert manager.show_session(session.session_id)["result_available"] is False


@pytest.mark.asyncio
async def test_mcp_wait_skips_result_already_collected_by_cli(tmp_path: pathlib.Path) -> None:
    """共有結果ファイルをCLI待機が回収済みの場合、MCP待機は同じ結果を再配送しない。"""
    manager, writer = _writer_backed_manager(tmp_path)
    collected = state.SessionState("terminal", str(tmp_path), engine="codex")
    running = state.SessionState("running", str(tmp_path), engine="codex")
    _complete(collected, message="回収済み本文")
    manager.sessions.update({collected.session_id: collected, running.session_id: running})
    writer.flush()

    claimed, error = retained_results.take_result(
        "root-session",
        collected.session_id,
        "root.json",
        collector="atk-agents-wait",
        state_root=tmp_path,
    )
    assert error is None
    assert claimed is not None and claimed["agent_message"] == "回収済み本文"

    response = await manager.wait()

    assert response["session_id"] == running.session_id
    assert "agent_message" not in response
    assert collected.result_delivered is True
    await manager.close()


@pytest.mark.asyncio
async def test_take_result_rejects_collection_by_another_writer(tmp_path: pathlib.Path) -> None:
    """公開した書込主体と異なる所有者の回収は、結果を返さず結果ファイルも削除しない。"""
    manager, writer = _writer_backed_manager(tmp_path)
    session = state.SessionState("terminal", str(tmp_path), engine="codex")
    _complete(session, message="所有者本文")
    manager.sessions[session.session_id] = session
    writer.flush()

    other, other_error = retained_results.take_result(
        "root-session",
        session.session_id,
        "other-writer.json",
        collector="atk-agents-wait",
        state_root=tmp_path,
    )

    assert other is None
    assert other_error is None
    assert writer.result_state(session.session_id) == "published"

    response = await manager.wait()

    assert response["session_id"] == session.session_id
    assert response["agent_message"] == "所有者本文"
    await manager.close()


@pytest.mark.asyncio
async def test_mcp_wait_collects_shared_result_exactly_once(tmp_path: pathlib.Path) -> None:
    """MCP待機は共有結果ファイルを1回だけ回収し、同じ結果を後続の回収処理へ残さない。"""
    manager, writer = _writer_backed_manager(tmp_path)
    session = state.SessionState("terminal", str(tmp_path), engine="codex")
    _complete(session, message="一度だけの本文")
    manager.sessions[session.session_id] = session
    writer.flush()
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session.session_id}.json"
    assert result_path.exists()

    first = await manager.wait()

    assert first["session_id"] == session.session_id
    assert first["agent_message"] == "一度だけの本文"
    assert not result_path.exists()
    assert writer.result_state(session.session_id) == "unpublished"

    again, again_error = retained_results.take_result(
        "root-session",
        session.session_id,
        "root.json",
        collector="atk-agents-wait",
        state_root=tmp_path,
    )

    assert again is None
    assert again_error is None
    await manager.close()


@pytest.mark.asyncio
async def test_wait_keeps_the_session_after_returning_the_terminal_result(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """waitは破棄の指定を受け取らず、終端結果を返した後もsessionを保持する。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("wait-keep", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session

    assert (await manager.wait())["status"] == "running"
    _complete(session)
    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.mcp"):
        response = await manager.wait()

    assert response["status"] == "completed"
    assert response["agent_message"] == "完了"
    assert session.session_id in manager.sessions
    assert session.session_id not in manager.stopped_sessions
    assert not backend.release_calls
    assert "result_collected session_id=wait-keep collector=mcp-wait" in caplog.text

    assert await manager.stop(session.session_id) == {}
    assert session.session_id in manager.stopped_sessions
    assert backend.release_calls == [session.session_id]
