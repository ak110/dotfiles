"""`_agents_server/manager_resume.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import datetime
import json
import logging
import pathlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    result_projection,
    resume_waits,
    session_registry,
    shared_layout,
    shared_roots,
    state,
    status_file,
    unavailable_candidates,
    wait_output_tracking,
)
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import claude_usage_limit, state_paths
from agent_toolkit._testing.agents_server_support import (
    _LAUNCH_VALUES,
    _OVERLOAD_ERROR,
    AssistantMessage,
    BlockingResultClaudeClient,
    BlockingResumeBackend,
    BlockingResumeClaudeClient,
    ConcurrentOwnerGoneBackend,
    FakeBackend,
    FakeClaudeClient,
    OverloadingBackend,
    ResultMessage,
    SystemMessage,
    ThreadReadBackend,
    UnavailableStartBackend,
    _blocking_resume_claude_manager,
    _complete,
    _expire_launched_session,
    _install_backend,
    _manager_with_fake,
    _publish_orphaned_codex,
    _publish_recovered_session,
    _rate_limit_event,
    _rejected_stream,
    _restart_with_registry_record,
    _resume_test_manager,
    _show_through_tool,
    _usage_limit_result,
    _usage_limited_manager,
    _use_real_plugin_preflight,
    _wait_until_terminal,
    _without_root,
    _write_failing_uv,
    install_backend,
)
from agent_toolkit._testing.helpers import delivery_payload

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_expired_explore_session_resumes_with_original_route_conditions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果保持期限後の再開でも探索フラグと除外集合を維持する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, backend = _manager_with_fake("codex")
    original_start = backend.start

    async def fail_first_start(
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        session = await original_start(prompt, cwd, model, effort, **kwargs)
        if model == "first":
            _complete(session, message="", error={"codexErrorInfo": "usageLimitExceeded"})
        return session

    monkeypatch.setattr(backend, "start", fail_first_start)
    second = await manager.start_explore("探索", str(tmp_path), model_type="medium_tier")
    session = manager.sessions[second["session_id"]]
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1

    response = await manager.send_message(session.session_id, "続行")

    resumed = manager.sessions[session.session_id]
    assert response["delivery"] == "reply_started"
    assert resumed.model_type == "medium_tier"
    assert resumed.launch_kind == "explore"
    assert resumed.excluded_candidates == frozenset({candidates[0]})


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_expired_multi_turn_session_resumes_and_agents_wait_observes_result(
    engine: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """両engineの複数turn後の期限切れ再開を番号指定の背景待機で観測する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = FakeBackend(manager.sessions, engine)
    _install_backend(manager, engine, backend)
    writer.activate()
    session = state.SessionState("saved-session", str(tmp_path), engine=engine, turn_seq=4)
    _complete(session, message="期限切れ結果")
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    response = await manager.send_message("saved-session", "続行")

    assert _without_root(response) == {"delivery": "reply_started", "label": ""}
    assert "previous_result" not in response
    assert backend.resume_calls == ["saved-session"]
    assert "saved-session" not in manager.expired_sessions

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

    resumed = manager.sessions[session.session_id]
    _complete(resumed, message="再開結果")
    writer.flush()

    assert await wait_task == 0
    result = json.loads(capsys.readouterr().out)
    assert result["agent_message"] == "再開結果"
    assert resumed.turn_seq == 5
    assert {"turn_seq", "finalized_at"}.isdisjoint(result)
    await manager.close()


@pytest.mark.asyncio
async def test_resumed_session_keeps_created_at_in_status_file(tmp_path: pathlib.Path) -> None:
    """再開したsessionは最初の開始時刻を保ち、状態ファイルへturnの開始時刻と別に書く。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = FakeBackend(manager.sessions, "claude")
    _install_backend(manager, "claude", backend)
    writer.activate()
    session = state.SessionState("created-session", str(tmp_path), engine="claude", turn_seq=2)
    session.created_at = "2026-09-25T21:58:13+00:00"
    _complete(session, message="前のturn")
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    assert _without_root(await manager.send_message("created-session", "続行")) == {"delivery": "reply_started", "label": ""}
    writer.flush()

    resumed = manager.sessions["created-session"]
    assert resumed.created_at == "2026-09-25T21:58:13+00:00"
    projected = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    entry = next(item for item in projected if item["session_id"] == "created-session")
    assert entry["created_at"] == "2026-09-25T21:58:13+00:00"
    assert entry["started_at"] != entry["created_at"]
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prepare_resume",
    [
        pytest.param(_expire_launched_session, id="retention-expired"),
        pytest.param(_restart_with_registry_record, id="registry-after-restart"),
    ],
)
async def test_resumed_session_keeps_every_launch_info_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    prepare_resume: Callable[[server_manager.AgentsServerManager, state.SessionState], None],
) -> None:
    """再開の処理によらず、起動情報の定義の全項目を`start`時の値のまま保つ。

    再開時に写されなかった項目は、`atk agents list`・`show`の状態と`atk agents wait`の終端行で空になり、
    labelで自sessionを探す手順と、`-review`の完了結果に付く採否確定の案内が成立しなくなる。
    再開後の登録簿にも同じ値を書き、もう一度再起動しても保たれることを確かめる。
    """
    assert set(_LAUNCH_VALUES) == {field.name for field in dataclasses.fields(session_registry.LaunchInfo)}
    manager, writer = _resume_test_manager(monkeypatch, tmp_path)
    session = state.SessionState(
        "launched-session", str(tmp_path), engine="claude", turn_seq=2, publish_registry=True, **_LAUNCH_VALUES
    )
    _complete(session, message="前のturn")
    prepare_resume(manager, session)

    assert _without_root(await manager.send_message(session.session_id, "続行")) == {
        "delivery": "reply_started",
        "label": _LAUNCH_VALUES["label"],
    }
    writer.flush()

    resumed = manager.sessions[session.session_id]
    assert resumed is not session
    assert {name: getattr(resumed, name) for name in _LAUNCH_VALUES} == _LAUNCH_VALUES
    projected = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    entry = next(item for item in projected if item["session_id"] == session.session_id)
    assert {name: entry[name] for name in _LAUNCH_VALUES} == _LAUNCH_VALUES
    shown = manager.show_session(session.session_id)
    assert (shown["label"], shown["prompt"]) == (_LAUNCH_VALUES["label"], _LAUNCH_VALUES["prompt"])
    resume_info = session_registry.resolve(session.session_id).resume_info
    assert resume_info is not None
    assert resume_info.launch_info == session_registry.LaunchInfo(**_LAUNCH_VALUES)

    _complete(resumed, message="再開後のturn")
    writer.flush()
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["session_id"], result["label"], result["status"]) == (
        session.session_id,
        _LAUNCH_VALUES["label"],
        "completed",
    )
    assert result_projection.REVIEW_RESULT_NEXT_ACTION in result["next_action"]
    await manager.close()


@pytest.mark.asyncio
async def test_resume_from_legacy_registry_record_without_launch_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動情報の項目を持たない版数2の登録簿レコードからも再開でき、labelとpromptは空文字列になる。"""
    manager, _ = _resume_test_manager(monkeypatch, tmp_path)
    session_registry.publish("legacy-session", terminal=True, engine="claude", cwd=str(tmp_path), turn_seq=2)

    assert _without_root(await manager.send_message("legacy-session", "続行")) == {"delivery": "reply_started", "label": ""}

    resumed = manager.sessions["legacy-session"]
    assert (resumed.label, resumed.prompt) == ("", "")
    await manager.close()


@pytest.mark.asyncio
async def test_owner_gone_resume_removes_previous_result_file(tmp_path: pathlib.Path) -> None:
    """所有主体終了による再開は新しいturnの開始時に旧結果を削除する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = ConcurrentOwnerGoneBackend(manager.sessions, "claude")
    install_backend(manager, "claude", backend)
    writer.activate()
    session = state.SessionState("claude-owner-gone", str(tmp_path), engine="claude", turn_seq=4)
    _complete(session, message="旧結果")
    manager.sessions[session.session_id] = session
    backend.owner_gone_session = session
    writer.flush()
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session.session_id}.json"

    responses = await asyncio.gather(
        manager.send_message(session.session_id, "先行指示", timeout=1),
        manager.send_message(session.session_id, "後続指示", timeout=1),
    )

    assert {response["delivery"] for response in responses} == {"reply_started", "steered"}
    assert manager.sessions[session.session_id].turn_seq == 5
    assert not result_path.exists()
    await manager.close()


@pytest.mark.asyncio
async def test_send_message_timeout_covers_resume(tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutは保存済みsessionの再開待ちにも適用する。"""
    manager = server_manager.AgentsServerManager()
    backend = BlockingResumeBackend(manager.sessions, "claude")
    install_backend(manager, "claude", backend)
    session = state.SessionState("claude-expired", str(tmp_path), engine="claude")
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    try:
        manager._start_resume(state.SessionResumeState.from_session(session), "準備指示", None)  # pylint: disable=protected-access
        await asyncio.wait_for(backend.resume_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match="send_message timed out: claude-expired"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)

        assert not backend.resume_calls
    finally:
        await manager.close()

    assert not manager._pending_resumes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_id",
    ["3468feae-b2bf-4d67-ac55-3c40207e8b5b", "claude-expired"],
)
async def test_kill_cancels_pending_resume_without_followup_message(
    session_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """再開入力のtimeout後は追加送信なしのkillで保留作業を回収する。"""
    manager = server_manager.AgentsServerManager()
    backend = BlockingResumeBackend(manager.sessions, "claude")
    install_backend(manager, "claude", backend)
    session = state.SessionState(session_id, str(tmp_path), engine="claude")
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    try:
        manager._start_resume(state.SessionResumeState.from_session(session), "準備指示", None)  # pylint: disable=protected-access
        await asyncio.wait_for(backend.resume_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match=f"send_message timed out: {session_id}"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)

        response = await manager.kill(session.session_id, timeout=1)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is False
        assert not manager._pending_resumes
        assert backend.resume_finished.is_set()
        assert backend.interrupt_calls == 0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_concurrent_owner_gone_send_messages_resume_once(tmp_path: pathlib.Path) -> None:
    """所有主体終了を同時検出した継続要求は再開を共有して両方を配送する。"""
    manager = server_manager.AgentsServerManager()
    backend = ConcurrentOwnerGoneBackend(manager.sessions, "claude")
    install_backend(manager, "claude", backend)
    session = state.SessionState("claude-race", str(tmp_path), engine="claude")
    manager.sessions[session.session_id] = session
    backend.owner_gone_session = session

    first, second = await asyncio.gather(
        manager.send_message(session.session_id, "先行指示", timeout=1),
        manager.send_message(session.session_id, "後続指示", timeout=1),
    )

    assert backend.resume_calls == [session.session_id]
    assert {first["delivery"], second["delivery"]} == {"reply_started", "steered"}


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["manager-one", "manager-two"])
async def test_each_manager_delivers_its_root_to_claude_start_and_resume(
    root: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """別の環境ownerがあっても、SDKの新規と再開は各Managerのrootに属する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "other-environment-root")
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity(root, "root.json", None), state_root=tmp_path)
    manager = server_manager.AgentsServerManager(writer)
    backend = manager._backend("claude")
    options_received: list[Any] = []

    def client_factory(options: Any) -> FakeClaudeClient:
        options_received.append(options)
        return FakeClaudeClient([[SystemMessage("claude-root-test"), ResultMessage("完了")]])

    monkeypatch.setattr(backend, "_client_factory", client_factory)
    try:
        session = await backend.start("新規", str(tmp_path))
        await backend.resume(session.session_id, state.ResumePrompt("再開"), str(tmp_path))
        assert len(options_received) == 2
        assert [options.env["AGENT_TOOLKIT_OWNER_SESSION"] for options in options_received] == [root, root]
        assert options_received[0].resume is None
        assert options_received[1].resume == session.session_id
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("query_started_before_timeout", [False, True])
async def test_claude_resume_timeout_drops_prompt_without_duplicate_resume(
    query_started_before_timeout: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """query開始の前後で期限切れになっても旧promptを配送せず、再開を重複しない。"""
    client = BlockingResumeClaudeClient()
    manager, _ = _blocking_resume_claude_manager(monkeypatch, client)
    session_id = "claude-pending"
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="claude",
    )

    pending = None
    initial_ticket = 0
    release_connect = asyncio.Event()
    try:
        if query_started_before_timeout:
            resume_state = manager.expired_sessions[session_id]
            pending, initial_ticket = manager._start_resume(resume_state, "準備指示", None)  # pylint: disable=protected-access
            await asyncio.wait_for(client.query_started.wait(), timeout=1)
        else:
            original_connect = client.connect

            async def blocked_connect() -> None:
                await release_connect.wait()
                await original_connect()

            monkeypatch.setattr(client, "connect", blocked_connect)

        with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
            await manager.send_message(session_id, "再開指示", timeout=0.01)

        if query_started_before_timeout:
            assert pending is not None
            pending.prompt.cancel(initial_ticket)
            await asyncio.wait_for(client.query_cancelled.wait(), timeout=1)
        else:
            assert not client.query_started.is_set()
            release_connect.set()
            client.release_query.set()
        assert not client.queries
        response = await manager.wait()
        assert set(response) == {"session_id", "status", "progress", "elapsed_seconds"}
        assert response["status"] == "running"
        assert response["progress"] == ""
        assert isinstance(response["elapsed_seconds"], int)
        assert response["elapsed_seconds"] >= 0
        assert session_id not in manager.expired_sessions

        response = await manager.send_message(session_id, "後続指示", timeout=1)

        assert response["delivery"] == "reply_started"
        assert client.connect_calls == 1
        assert [delivery_payload(value) for value in client.queries] == ["後続指示"]
    finally:
        client.release_query.set()
        client.stop_stream.set()
        await manager.close()


@pytest.mark.asyncio
async def test_claude_kill_cleans_owned_pending_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留resumeのkillは応答前にClaude所有taskとclientを回収する。"""
    client = BlockingResumeClaudeClient()
    manager, backend = _blocking_resume_claude_manager(monkeypatch, client)
    session_id = "claude-pending"
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="claude",
    )

    try:
        manager._start_resume(manager.expired_sessions[session_id], "準備指示", None)  # pylint: disable=protected-access
        await asyncio.wait_for(client.query_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
            await manager.send_message(session_id, "再開指示", timeout=0.01)

        assert backend._tasks
        response = await manager.kill(session_id, timeout=1)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is False
        assert not manager._pending_resumes
        assert not backend._tasks
        assert client.disconnected is True
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_pending_resume_retains_previous_result_after_retention_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開待機中に保持期限を越えた直前結果を後続応答へ含める。"""
    client = BlockingResumeClaudeClient()
    manager, _ = _blocking_resume_claude_manager(monkeypatch, client)
    session_id = "claude-pending"
    session = state.SessionState(session_id, str(tmp_path), engine="claude")
    session.status = "completed"
    session.turn_completed = True
    session.agent_message = "期限付き結果"
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.04
    original_deadline = session.retention_deadline
    manager.sessions[session_id] = session

    try:
        pending, initial_ticket = manager._start_resume(  # pylint: disable=protected-access
            state.SessionResumeState.from_session(session), "準備指示", session.previous_result()
        )
        await asyncio.wait_for(client.query_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
            await manager.send_message(session_id, "期限前指示", timeout=0.01)

        pending.prompt.cancel(initial_ticket)
        await asyncio.wait_for(client.query_cancelled.wait(), timeout=0.1)
        await asyncio.sleep(max(0.0, original_deadline - asyncio.get_running_loop().time()) + 0.01)
        response = await manager.send_message(session_id, "期限後指示", timeout=1)

        assert asyncio.get_running_loop().time() >= original_deadline
        assert response["previous_result"]["agent_message"] == "期限付き結果"
        assert client.connect_calls == 1
        assert [delivery_payload(value) for value in client.queries] == ["期限後指示"]
    finally:
        client.release_query.set()
        client.stop_stream.set()
        await manager.close()


@pytest.mark.asyncio
async def test_claude_resume_owns_saved_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claude backendがresume後の同じsession IDを新しい所有タスクへ登録する。"""
    client = FakeClaudeClient([[SystemMessage("claude-saved"), ResultMessage("再開結果")]])
    captured: dict[str, str | None] = {}

    def build_options(_cwd: str, _model: str | None, _effort: str | None, session_id: str | None = None, **_kwargs: Any) -> Any:
        captured["session_id"] = session_id
        return SimpleNamespace()

    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", build_options)
    try:
        prompt = state.ResumePrompt("続行")
        session = await manager.resume("claude-saved", prompt, str(tmp_path), "model", "high")
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert captured == {"session_id": "claude-saved"}
        assert session.session_id == "claude-saved"
        assert session.agent_message == "再開結果"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_resume_replaces_retained_terminal_state_while_new_turn_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resumeは登録簿に残る前turnの終端状態を新しいturnへ引き継がない。"""
    session_id = "claude-retained"
    client = BlockingResultClaudeClient(session_id)
    manager = claude_backend.ClaudeServerManager(client_factory=lambda _options: client)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    retained = state.SessionState(session_id, str(tmp_path), engine="claude")
    _complete(retained, message="前turnの結果")
    manager.sessions[session_id] = retained
    try:
        prompt = state.ResumePrompt("続行")
        resumed = await manager.resume(session_id, prompt, str(tmp_path))

        assert resumed is not retained
        assert manager.sessions[session_id] is resumed
        assert resumed.status == "running"
        assert resumed.agent_message == ""
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_auto_resume_delivery_failure_terminates_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """自動再開の配送失敗を保留本文と復旧情報付きの失敗として返す。"""
    _publish_recovered_session(monkeypatch, tmp_path, "child-terminal", "completed")
    manager = server_manager.AgentsServerManager(status_writer=None)
    backend = FakeBackend(manager.sessions, "codex")
    _install_backend(manager, "codex", backend)
    session = state.SessionState("parent-session", str(tmp_path), engine="codex")
    session.live_child_session_ids.add("child-terminal")
    resume_waits.begin_auto_resume_wait(
        session,
        {"status": "completed", "agent_message": "保留していた本文", "error": None},
    )
    manager.sessions[session.session_id] = session

    async def fail_delivery(_session: state.SessionState, _prompt: str) -> dict[str, Any]:
        backend.send_calls += 1
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(backend, "send_message", fail_delivery)

    response = await manager.wait()

    assert response["status"] == "failed"
    assert response["agent_message"] == "保留していた本文"
    assert response["error"] == {
        "message": "RuntimeError: delivery failed",
        "unobservedSessions": ["child-terminal"],
    }
    assert session.awaiting_auto_resume is False
    assert session.pending_result is None
    assert session.auto_resume_consumed is True
    assert backend.send_calls == 1
    await manager.close()


@pytest.mark.asyncio
async def test_wait_delivers_child_session_result_without_kill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別プロセスが起動した子sessionの終端後、killを用いないwaitが委譲先の結果を配送する。

    子sessionの登録簿レコードが残らない場合も、共有の終端結果ファイルから終端を判定する。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("claude", "model", "high")])
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = FakeBackend(manager.sessions, "claude")
    _install_backend(manager, "claude", backend)
    writer.activate()
    started = await manager.start("high_tier", "委譲する", str(tmp_path))
    delegate = manager.sessions[started["session_id"]]

    # 委譲先が別プロセスのMCPサーバーへstart_exploreを発行した状態を模す。
    child_writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "delegate-host.json", "delegate-host"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    child_writer.activate()
    child = state.SessionState("grandchild", str(tmp_path), engine="claude", launch_kind="explore")
    child_writer.sessions[child.session_id] = child
    wait_output_tracking.consume_agents_server_tool_result(
        delegate,
        "mcp__agents_server__start_explore",
        {},
        {"session_id": child.session_id, "status": "running"},
    )
    resume_waits.begin_auto_resume_wait(
        delegate,
        {"status": "completed", "agent_message": "子sessionの結果を待つ本文", "error": None},
    )

    _complete(child, message="子sessionの結果")
    child_writer.flush()
    # 所有側が保持期限で解放した後も、終端結果ファイルを第2の根拠に終端と判定する。
    session_registry.release(child.session_id, reason="retention_expired")

    pending = await manager.wait()

    # 子sessionの終端を観測した待機が、killを伴わずに継続指示を1回配送する。
    assert pending["status"] == "running"
    assert backend.send_calls == 1
    assert (shared_layout.results_directory("root-session", tmp_path) / f"{child.session_id}.json").is_file()

    _complete(delegate, message="子sessionを確認した最終結果")
    response = await manager.wait()

    assert response["status"] == "completed"
    assert response["agent_message"] == "子sessionを確認した最終結果"
    assert "error" not in response
    child_writer.deactivate()
    await manager.close()


@pytest.mark.asyncio
async def test_send_message_resumes_stopped_session(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """破棄後も別保持先の最小状態から同じsessionを暗黙再開する。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("stopped", str(tmp_path), engine="codex")
    _complete(session)
    manager.sessions[session.session_id] = session
    await manager.stop(session.session_id)

    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.mcp"):
        response = await manager.send_message(session.session_id, "再開")

    assert response["delivery"] == "reply_started"
    assert "previous_result" not in response
    assert backend.resume_calls == [session.session_id]
    assert session.session_id not in manager.stopped_sessions
    assert "session_transition event=resume session_id=stopped writer=mcp-manager status=running" in caplog.text


@pytest.mark.asyncio
async def test_resume_rejects_cwd_where_plugin_commands_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """終端済みsessionの再開でも、起動コマンドが失敗する作業ディレクトリでは委譲先を再開しない。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("saved-session", str(tmp_path), engine="codex", turn_seq=1)
    _complete(session, message="前のturn")
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session
    _use_real_plugin_preflight(monkeypatch)
    bin_dir = tmp_path / "bin"
    _write_failing_uv(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir))

    with pytest.raises(ValueError, match="プラグインの起動コマンドが失敗"):
        await manager.send_message("saved-session", "続行")

    assert not backend.resume_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_unobserved"),
    [("completed", None), ("running", ["child-session"])],
)
async def test_child_collected_by_background_agents_wait_is_not_unobserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    status: str,
    expected_unobserved: list[str] | None,
) -> None:
    """登録簿と終端結果ファイルが消えた後も、背景待機の出力ファイルに終端行がある孫sessionは未観測にしない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    manager = server_manager.AgentsServerManager(status_writer=None)
    parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
    parent.live_child_session_ids.add("child-session")
    output = tmp_path / "bqwu17av0.output"
    output.write_text(json.dumps({"session_id": "child-session", "status": status}) + "\n", encoding="utf-8")
    wait_output_tracking.consume_agents_wait_output(
        parent, f"Command running in background with ID: bqwu17av0. Output is being written to: {output}. You will be notified."
    )
    resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
    manager.sessions[parent.session_id] = parent

    await manager._advance_child_session_wait(parent)  # pylint: disable=protected-access

    error = parent.error if isinstance(parent.error, dict) else {}
    assert error.get("unobservedSessions") == expected_unobserved
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["in-progress-after", "recorded-turn-running", "live-status-file", "query-failure", "claude-engine"]
)
async def test_orphaned_record_keeps_turn_unobserved_unless_takeover_conditions_hold(
    case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """所有者の生存、進行中のturn、照会の失敗、Codex以外のengineでは引き継がず、従来どおり実行中の可能性として拒否する。

    引き継ぐと、別のプロセスが実行中のturnへ同じsessionの新しいturnを重ねて起動する。
    """
    session_id = "01a0f953-44f1-7c23-91ce-7350e83c57ab"
    _publish_orphaned_codex(monkeypatch, tmp_path, session_id, "turn-1")
    turns: list[tuple[str, str]] | None = [("turn-1", "interrupted")]
    error: Exception | None = None
    if case == "in-progress-after":
        turns = [("turn-1", "interrupted"), ("turn-2", "inProgress")]
    elif case == "recorded-turn-running":
        turns = [("turn-1", "inProgress")]
    elif case == "query-failure":
        error = codex_backend.AppServerError("thread/read failed")
    elif case == "live-status-file":
        directory = shared_layout.status_directory("owner-root", tmp_path)
        directory.mkdir(parents=True)
        heartbeat = datetime.datetime.now(datetime.UTC).isoformat()
        (directory / "root.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "host_session_id": None,
                    "heartbeat_at": heartbeat,
                    "updated_at": heartbeat,
                    "sessions": [{"session_id": session_id, "status": "running"}],
                }
            ),
            encoding="utf-8",
        )
    else:
        session_registry.publish(session_id, terminal=False, engine="claude", cwd=str(tmp_path), status="running")
    manager = server_manager.AgentsServerManager(None)
    backend = ThreadReadBackend(manager.sessions, turns, error)
    _install_backend(manager, "codex", backend)

    with pytest.raises(ValueError, match="error.recovery=turn_unobserved"):
        await manager.send_message(session_id, "続行")
    with pytest.raises(ValueError, match="another writer"):
        await _show_through_tool(monkeypatch, manager, session_id)
    assert session_registry.resolve(session_id).state is session_registry.Resolution.RUNNING
    assert not backend.resume_calls
    assert (not backend.read_calls) is (case in {"live-status-file", "claude-engine"})
    await manager.close()


@pytest.mark.asyncio
async def test_overloaded_turn_resumes_same_session_and_publishes_resumed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動後の過負荷は失敗を公開せず、待機の後に同じsessionで継続し、継続したturnの結果を終端結果として返す。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m: [("codex", "gpt-6.1-sol", "high")])
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(resume_waits, "OVERLOAD_RESUME_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    manager = server_manager.AgentsServerManager()
    backend = OverloadingBackend(manager.sessions, manager._condition, overloads=2)
    _install_backend(manager, "codex", backend)

    started = await manager.start("high_tier", "作業する", str(tmp_path))
    response = await _wait_until_terminal(manager)

    assert started["status"] == "running"
    assert response["session_id"] == started["session_id"]
    assert response["status"] == "completed"
    assert response["agent_message"] == "継続後の結果"
    assert backend.send_calls == 2
    assert all("serverOverloaded" in prompt and "所定の返却形式" in prompt for prompt in backend.prompts[1:])
    assert manager.sessions[started["session_id"]].turn_seq == 3
    assert not unavailable_candidates.load_unavailable_candidates(
        "high_tier", "delegate", now=datetime.datetime.now(datetime.UTC)
    )
    await manager.close()


@pytest.mark.asyncio
async def test_overload_wait_is_visible_as_running_with_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """待機中の`show`と`list`は`running`、`api_error`の種別`serverOverloaded`と結果の保留を返す。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m: [("codex", "gpt-6.1-sol", "high")])
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(resume_waits, "OVERLOAD_RESUME_DELAYS_SECONDS", (3600.0, 3600.0, 3600.0))
    manager = server_manager.AgentsServerManager()
    backend = OverloadingBackend(manager.sessions, manager._condition, overloads=1)
    _install_backend(manager, "codex", backend)

    started = await manager.start("high_tier", "作業する", str(tmp_path))
    await asyncio.gather(*backend.pending)
    shown = manager.show_session(started["session_id"])
    listed = manager.list_sessions()

    assert shown["status"] == "running"
    assert shown["result_held"] is True
    assert shown["api_error"]["type"] == "serverOverloaded"
    assert shown["api_error"]["http_status"] is None
    listed_session = next(item for item in listed["sessions"] if item["session_id"] == started["session_id"])
    assert listed_session["status"] == "running"
    assert listed_session["api_error"]["type"] == "serverOverloaded"
    assert backend.send_calls == 0
    await manager.close()


@pytest.mark.asyncio
async def test_overload_limit_publishes_last_failure_and_excludes_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """3回の継続も過負荷で終われば、最後の失敗を現行の形で公開し、候補を次回の除外対象に記録する。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m: [("codex", "gpt-6.1-sol", "high")])
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(resume_waits, "OVERLOAD_RESUME_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    manager = server_manager.AgentsServerManager()
    backend = OverloadingBackend(manager.sessions, manager._condition, overloads=4)
    _install_backend(manager, "codex", backend)

    started = await manager.start("high_tier", "作業する", str(tmp_path))
    response = await _wait_until_terminal(manager)

    assert response["status"] == "failed"
    assert response["error"]["codexErrorInfo"] == "serverOverloaded"
    assert response["error"] == _OVERLOAD_ERROR
    assert backend.send_calls == 3
    assert manager.sessions[started["session_id"]].turn_seq == 4
    assert unavailable_candidates.load_unavailable_candidates(
        "high_tier", "delegate", now=datetime.datetime.now(datetime.UTC)
    ) == {("codex", "gpt-6.1-sol", "high"): "serverOverloaded"}
    await manager.close()


@pytest.mark.asyncio
async def test_overload_within_availability_check_switches_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動の可用性確認の間に過負荷で終端した候補は、自動継続せず次の候補への切替で扱う。"""
    candidates = [("codex", "gpt-6.1-sol", "high"), ("codex", "gpt-6-sol", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m: candidates)
    manager = server_manager.AgentsServerManager()
    _install_backend(manager, "codex", UnavailableStartBackend(manager.sessions, "codex", error=dict(_OVERLOAD_ERROR)))

    response = await manager.start("high_tier", "作業する", str(tmp_path))

    assert response["status"] == "failed"
    assert [item["reason"] for item in response["excluded_candidates"]] == ["serverOverloaded", "serverOverloaded"]
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit_type", ["seven_day", "seven_day_opus", "seven_day_sonnet", "five_hour"])
@pytest.mark.parametrize("before_init", [True, False])
async def test_claude_usage_limit_waits_and_resumes_same_session(
    limit_type: str,
    before_init: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動直後にWeekly limitか5時間の利用上限で拒否されても失敗を公開せず、解除後に同じsessionで続けた結果を返す。"""
    monkeypatch.setattr(claude_usage_limit, "RECHECK_SECONDS", 0.05)
    streams = [
        _rejected_stream(limit_type, None, before_init=before_init, session_id="claude-limited"),
        [_rate_limit_event("allowed", limit_type), AssistantMessage("再開後"), ResultMessage("継続後の結果")],
    ]
    manager, client, codex = await _usage_limited_manager(monkeypatch, streams)
    try:
        started = await manager.start("high_tier", "作業する", str(tmp_path))
        response = await _wait_until_terminal(manager)

        assert started["status"] == "running"
        assert started["engine"] == "claude"
        assert "excluded_candidates" not in started
        assert response["session_id"] == "claude-limited"
        assert response["status"] == "completed"
        assert response["agent_message"] == "継続後の結果"
        assert codex.start_calls == []
        assert len(client.queries) == 2
        assert limit_type in client.queries[1] and "所定の返却形式" in client.queries[1]
        assert not unavailable_candidates.load_unavailable_candidates(
            "high_tier", "delegate", now=datetime.datetime.now(datetime.UTC)
        )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_usage_limit_keeps_waiting_while_rejection_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続のturnも拒否される間は待機へ戻り、回数で打ち切らずに解除後の結果を返す。"""
    monkeypatch.setattr(claude_usage_limit, "RECHECK_SECONDS", 0.02)
    rejected = [_rate_limit_event("rejected", "seven_day"), _usage_limit_result()]
    streams = [
        _rejected_stream("seven_day", None, before_init=False, session_id="claude-repeated"),
        *[list(rejected) for _ in range(4)],
        [AssistantMessage("再開後"), ResultMessage("解除後の結果")],
    ]
    manager, client, codex = await _usage_limited_manager(monkeypatch, streams)
    try:
        await manager.start("high_tier", "作業する", str(tmp_path))
        response = await _wait_until_terminal(manager, attempts=100)

        assert response["status"] == "completed"
        assert response["agent_message"] == "解除後の結果"
        assert len(client.queries) == 6
        assert codex.start_calls == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_usage_limit_wait_is_visible_and_kill_returns_held_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解除待ちの間は`show`・`list`が`running`と種類・解除予定時刻を返し、`kill`は保留した結果で終端して候補を除外しない。"""
    resets_at = int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600
    streams = [_rejected_stream("seven_day_opus", resets_at, before_init=False, session_id="claude-visible")]
    manager, client, codex = await _usage_limited_manager(monkeypatch, streams)
    try:
        started = await manager.start("high_tier", "作業する", str(tmp_path))
        shown = manager.show_session(started["session_id"])
        listed = next(item for item in manager.list_sessions()["sessions"] if item["session_id"] == started["session_id"])

        assert started["status"] == "running"
        assert shown["status"] == "running"
        assert shown["result_held"] is True
        expected_reset = datetime.datetime.fromtimestamp(resets_at, datetime.UTC).isoformat()
        assert shown["api_error"]["type"] == "usage_limit"
        assert shown["api_error"]["limit_type"] == "seven_day_opus"
        assert shown["api_error"]["resets_at"] == expected_reset
        assert listed["status"] == "running"
        assert listed["api_error"]["limit_type"] == "seven_day_opus"
        assert len(client.queries) == 1

        killed = await manager.kill(started["session_id"])

        assert killed["status"] == "failed"
        assert killed["error"]["usageLimit"] == {"type": "seven_day_opus", "resetsAt": expected_reset}
        assert codex.start_calls == []
        assert not unavailable_candidates.load_unavailable_candidates(
            "high_tier", "delegate", now=datetime.datetime.now(datetime.UTC)
        )
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_usage_limit_wait_accepts_send_message_as_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解除待ちの間に委譲元が`send_message`を送ると、保留を確定してreplyとして配送する。"""
    resets_at = int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600
    streams = [
        _rejected_stream("five_hour", resets_at, before_init=False, session_id="claude-reply"),
        [AssistantMessage("返信"), ResultMessage("返信の結果")],
    ]
    manager, client, _codex = await _usage_limited_manager(monkeypatch, streams)
    try:
        started = await manager.start("high_tier", "作業する", str(tmp_path))
        await manager.send_message(started["session_id"], "続けて")
        response = await _wait_until_terminal(manager)

        assert response["status"] == "completed"
        assert response["agent_message"] == "返信の結果"
        assert "続けて" in client.queries[1]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_claude_overage_rejection_still_switches_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """待機の対象外（`overage`）の拒否は従来どおり次の候補へ切り替え、新しい形式の理由で記録する。"""
    streams = [_rejected_stream("overage", None, before_init=False, session_id="claude-overage")]
    manager, _client, codex = await _usage_limited_manager(monkeypatch, streams)
    # 切替先のCodex候補はモデル出力を返さないため、可用性確認の上限まで待つ時間を短くする。
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.3)
    try:
        response = await manager.start("high_tier", "作業する", str(tmp_path))

        assert response["engine"] == "codex"
        assert codex.start_calls == [("gpt-6.1-sol", "high", "delegate")]
        assert [item["reason"] for item in response["excluded_candidates"]] == ["429:overage"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_legacy_claude_rate_limit_record_does_not_skip_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """旧版がClaudeの429へ記録した除外理由`429`では候補を除外せず、その候補で起動し直す。"""
    unavailable_candidates.record_unavailable_candidate(
        "high_tier", "delegate", ("claude", "opus", "high"), "429", now=datetime.datetime.now(datetime.UTC)
    )
    streams = [[SystemMessage("claude-legacy"), AssistantMessage("進行"), ResultMessage("完了")]]
    manager, _client, codex = await _usage_limited_manager(monkeypatch, streams)
    try:
        response = await manager.start("high_tier", "作業する", str(tmp_path))

        assert response["engine"] == "claude"
        assert "excluded_candidates" not in response
        assert codex.start_calls == []
    finally:
        await manager.close()
