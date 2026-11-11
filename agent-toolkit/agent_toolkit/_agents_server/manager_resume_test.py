"""`_agents_server/manager_resume.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import (
    agents_wait,
    launch_requests,
    result_projection,
    shared_layout,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_support import (
    _LAUNCH_VALUES,
    ConcurrentOwnerGoneBackend,
    FakeBackend,
    _complete,
    _expire_launched_session,
    _install_backend,
    _restart_with_registry_record,
    _resume_test_manager,
    _without_root,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("projection_failure", [False, True], ids=["projection-ready", "projection-failed"])
@pytest.mark.asyncio
async def test_cold_reply_publishes_wait_target_before_returning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    projection_failure: bool,
) -> None:
    """再開応答の直後に公開CLIで待つと、登録済みの同じsessionとして待機できる。"""
    for name in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "AGENT_TOOLKIT_STATUS_HOST_SESSION"):
        monkeypatch.delenv(name, raising=False)
    # 明示rootを受け取る最上位CLIの入力とし、主体不明の委譲先へ読み替えない。
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)

    async def ready_commands(_cwd: str) -> None:
        """バックエンドを代用する試験ではホストCLIの準備を行わない。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready_commands)
    async with _resume_test_manager(monkeypatch, tmp_path) as (manager, writer):
        # 定期保存の時機に依存せず、公開応答自身が状態公開を完了することを確かめる。
        monkeypatch.setattr(writer, "_aggregate_seconds", 600)
        identifier = "cold-public-wait-session"
        previous = state.SessionState(identifier, str(tmp_path), engine="codex", turn_seq=1)
        _complete(previous, message="前turn")
        previous.result_delivered = True
        manager.expired_sessions[identifier] = state.SessionResumeState.from_session(previous)
        backend = FakeBackend(manager.sessions, "codex")
        backend.publish_registry = True
        install_backend(manager, "codex", backend)
        original_write = status_file.atomic_write

        def reject_projection(path: pathlib.Path, content: str, **kwargs: Any) -> None:
            if path == writer.path:
                raise PermissionError("表示projectionの保存を拒否")
            original_write(path, content, **kwargs)

        with monkeypatch.context() as projection_patch:
            if projection_failure:
                projection_patch.setattr(status_file, "atomic_write", reject_projection)
            response = await manager.send_message(identifier, "同じ依頼を続行せよ")
            assert response["delivery"] == "reply_started"
            with pytest.raises(SystemExit) as exited:
                atk.main(["agents", "wait", "--root-session-id", response["root_session_id"]])
            assert exited.value.code == 3
            assert "待機対象の登録が0件" not in capsys.readouterr().err


@pytest.mark.parametrize("origin", ["stopped", "expired", "owner-gone"])
@pytest.mark.parametrize("outcome", ["completed", "execution-failed", "start-failed", "ambiguous"])
@pytest.mark.parametrize("finish_before_response", [True, False])
@pytest.mark.asyncio
async def test_reply_delivery_is_independent_of_turn_terminal(
    origin: str,
    outcome: str,
    finish_before_response: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """停止後・保持期限後・所有主体消失後の再開は、配送結果と前後の結果を応答順序によらず分離する。"""
    async with _resume_test_manager(monkeypatch, tmp_path) as (manager, writer):
        identifier = "reply-order-session"
        previous = state.SessionState(identifier, str(tmp_path), engine="codex", publish_registry=True, turn_seq=1)
        _complete(previous, message="前turnの結果")
        await previous.drain_registration()
        await writer.retain_result_async(previous)
        saved = state.SessionResumeState.from_session(previous)
        if origin == "stopped":
            manager.stopped_sessions[identifier] = saved
        elif origin == "expired":
            manager.expired_sessions[identifier] = saved
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)

        def finish(session: state.SessionState) -> None:
            _complete(session, message="新turnの結果", error={"message": "実行失敗"} if outcome == "execution-failed" else None)

        async def resume(
            session_id: str, prompt: state.ResumePrompt, cwd: str, model: str | None, effort: str | None, **_kwargs: Any
        ) -> state.SessionState:
            session = state.SessionState(session_id, cwd, model=model, effort=effort, engine="codex", turn_seq=2)
            manager.sessions[session_id] = session
            state.initialize_turn(session)
            if outcome in {"start-failed", "ambiguous"}:
                prompt.record_delivery("reply_failed" if outcome == "start-failed" else "reply_ambiguous")
                if outcome == "start-failed":
                    _complete(session, message="", error={"message": "開始不能"})
                else:
                    session.record_turn_ambiguous()
            else:

                async def accept(_text: str) -> None:
                    if finish_before_response:
                        finish(session)

                await prompt.deliver(accept)
            return session

        monkeypatch.setattr(backend, "resume", resume)
        response = await manager.send_message(identifier, "再開要求")
        expected = {"start-failed": "reply_failed", "ambiguous": "reply_ambiguous"}.get(outcome, "reply_started")
        assert response["delivery"] == expected
        assert response["previous_result"]["agent_message"] == "前turnの結果"
        session = manager.sessions[identifier]
        if outcome in {"completed", "execution-failed"}:
            if not finish_before_response:
                finish(session)
            await writer.flush_async()
            result = writer.read_result(identifier)
            assert result is not None and result["agent_message"] == "新turnの結果"
            assert result["status"] == ("failed" if outcome == "execution-failed" else "completed")
            assert (
                await asyncio.to_thread(
                    agents_wait.wait_for_result,
                    environment={"CLAUDE_CODE_SESSION_ID": writer.root_session_id},
                    state_root=writer.state_root,
                )
                == 0
            )
            collected = json.loads(capsys.readouterr().out)
            assert collected["agent_message"] == "新turnの結果"
        assert bool(session.followup_prompts) is (expected != "reply_failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_expired_multi_turn_session_resumes_and_agents_wait_observes_result(
    engine: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """両engineで、複数turn後に保持期限に達したsessionを再開し、その結果を`atk agents wait`の待機で観測する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, engine)
        _install_backend(manager, engine, backend)
        writer.activate()
        session = state.SessionState("saved-session", str(tmp_path), engine=engine, turn_seq=4)
        _complete(session, message="期限切れ結果")
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[session.session_id] = session

        response = await manager.send_message("saved-session", "続行")

        assert _without_root(response) == {
            "delivery": "reply_started",
            "label": "",
            "previous_result": {"status": "completed", "agent_message": "期限切れ結果"},
        }
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
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "claude")
        _install_backend(manager, "claude", backend)
        writer.activate()
        session = state.SessionState("created-session", str(tmp_path), engine="claude", turn_seq=2)
        session.created_at = "2026-09-25T21:58:13+00:00"
        _complete(session, message="前のturn")
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[session.session_id] = session

        response = await manager.send_message("created-session", "続行")
        assert response["delivery"] == "reply_started"
        assert response["previous_result"]["agent_message"] == "前のturn"
        writer.flush()

        resumed = manager.sessions["created-session"]
        assert resumed.created_at == "2026-09-25T21:58:13+00:00"
        projected = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
        entry = next(item for item in projected if item["session_id"] == "created-session")
        assert entry["created_at"] == "2026-09-25T21:58:13+00:00"
        assert entry["started_at"] != entry["created_at"]
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["lane-01-exec-review", "lane-01-reader-fit-review"])
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
    label: str,
    prepare_resume: Callable[[server_manager.AgentsServerManager, state.SessionState], None],
) -> None:
    """再開の処理によらず、起動情報の定義の全項目を`start`時の値のまま保つ。

    再開時に写されなかった項目は、`atk agents list`・`show`の状態と`atk agents wait`の終了行で空になり、
    labelで自sessionを探す手順と、`-review`の完了結果に付く採否確定の案内が成立しなくなる。
    再開後の登録簿にも同じ値を書き、もう一度再起動しても保たれることを確かめる。
    """
    expected_launch = {**_LAUNCH_VALUES, "label": label}
    launch_values: dict[str, Any] = {
        **expected_launch,
        "followup_prompts": ("先行追送",),
        "final_responses": ((1, "先行応答"),),
        "terminal_error": None,
        "effort_levels": ("low", "medium", "high"),
        "effort_history": (("2026-09-25T21:58:14+00:00", "low", "medium", "parent"),),
    }
    assert set(launch_values) == {field.name for field in dataclasses.fields(session_registry.LaunchInfo)}
    async with _resume_test_manager(monkeypatch, tmp_path) as (manager, writer):
        session = state.SessionState(
            "launched-session", str(tmp_path), engine="claude", turn_seq=2, publish_registry=True, **launch_values
        )
        _complete(session, message="前のturn")
        expected_info = dataclasses.replace(session_registry.LaunchInfo.of(session), followup_prompts=("先行追送", "続行"))
        # 再起動を模す前に、旧所有者が作成する登録の保存を完了する。
        await session.drain_registration()
        prepare_resume(manager, session)

        response = await manager.send_message(session.session_id, "続行")
        assert response["delivery"] == "reply_started"
        assert response["label"] == label
        if prepare_resume is _expire_launched_session:
            assert response["previous_result"]["agent_message"] == "前のturn"
        else:
            assert "previous_result" not in response
        writer.flush()

        resumed = manager.sessions[session.session_id]
        assert resumed is not session
        assert {name: getattr(resumed, name) for name in expected_launch} == expected_launch
        projected = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
        entry = next(item for item in projected if item["session_id"] == session.session_id)
        assert {name: entry[name] for name in expected_launch} == expected_launch
        shown = await manager.show_session(session.session_id)
        assert (shown["label"], shown["prompt"]) == (label, expected_launch["prompt"])
        resume_info = session_registry.resolve(session.session_id).resume_info
        assert resume_info is not None
        assert session_registry.LaunchInfo.of(resumed) == expected_info
        assert resume_info.launch_info == expected_info

        _complete(resumed, message="再開後のturn")
        writer.flush()
        assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
        result = json.loads(capsys.readouterr().out)
        assert (result["session_id"], result["label"], result["status"]) == (
            session.session_id,
            label,
            "completed",
        )
        if label.endswith("reader-fit-review"):
            assert "`reader-fit-review.parent.md`「受領」" in result["next_action"]
            assert "reviewee.md" not in result["next_action"]
        else:
            assert result_projection.REVIEW_RESULT_NEXT_ACTION in result["next_action"]
        await manager.close()


@pytest.mark.asyncio
async def test_resume_from_legacy_registry_record_without_launch_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動情報の項目を持たない版数2の登録簿レコードからも再開でき、labelとpromptは空文字列になる。"""
    async with _resume_test_manager(monkeypatch, tmp_path) as (manager, _):
        session_registry.publish(
            "legacy-session",
            state=session_registry.PublicationState.RESULT_COMPLETED,
            engine="claude",
            cwd=str(tmp_path),
            turn_seq=2,
        )

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
    async with server_manager.AgentsServerManager(writer) as manager:
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
