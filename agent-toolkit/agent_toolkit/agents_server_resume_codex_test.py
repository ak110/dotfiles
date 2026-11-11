# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""Codex backendの子session追跡と再開結果の公開を検証する。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import os
import pathlib
import sys
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_layout, shared_roots, state, status_file
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_resume_support import (
    _RESUME_WAIT_TIMEOUT,
    _STATE_TIMEOUT,
    _await_state,
    _codex_parent_with_child,
    _complete_codex_turn_with_child,
    _wait_with_timeout,
)
from agent_toolkit._testing.agents_server_resume_support import _isolate_session_registry as _isolate_session_registry
from agent_toolkit._testing.agents_server_support import install_backend


@pytest.mark.asyncio
async def test_run_resume_removes_previous_result_file(tmp_path: pathlib.Path) -> None:
    """状態writerを伴う再開はbackend応答後に前turnの結果を削除する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:

        class ResumeBackend:
            async def resume(
                self,
                session_id: str,
                _prompt: state.ResumePrompt,
                cwd: str,
                model: str | None,
                effort: str | None,
                **kwargs: Any,
            ) -> state.SessionState:
                resumed = state.SessionState(
                    session_id,
                    cwd,
                    model=model,
                    effort=effort,
                    engine="codex",
                    model_type=kwargs["model_type"],
                    turn_seq=kwargs["turn_seq"] + 1,
                )
                manager.sessions[session_id] = resumed
                return resumed

            async def close(self) -> None:
                """外部資源を持たないため何もしない。"""

        install_backend(manager, "codex", ResumeBackend())
        source = state.SessionState(
            "resume-result",
            str(tmp_path),
            model="model",
            effort="medium",
            engine="codex",
            model_type="execute",
            turn_seq=1,
        )
        result_path = shared_layout.results_directory("root", tmp_path) / f"{source.session_id}.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}\n", encoding="utf-8")
        writer.activate()
        resumed = await manager._run_resume(
            state.SessionResumeState.from_session(source),
            state.ResumePrompt("続行"),
        )

        assert resumed.turn_seq == 2
        assert not result_path.exists()


@pytest.mark.asyncio
async def test_codex_child_session_holds_result_until_single_auto_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codexは孫sessionが残るturnの待機表明を公開せず保留し、孫の終了後に同じsessionを一度だけ再開する。

    委譲元は手動の再開指示なしに、再開したturnの完了報告を受け取る。
    """
    async with _codex_parent_with_child(tmp_path) as (manager, backend, session, _writer):
        child_session_id = "codex-child"
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        await _complete_codex_turn_with_child(backend, session, child_session_id)

        assert session.awaiting_auto_resume is True
        assert session.result_available is False
        assert session.status == "running"
        assert session.pending_result is not None
        assert session.pending_result["agent_message"] == f"待機中: {child_session_id}"
        assert session.live_child_session_ids == {child_session_id}

        prompts: list[str] = []

        async def resume(target: state.SessionState, prompt: str) -> dict[str, Any]:
            prompts.append(prompt)
            state.begin_reply(target)
            target.status = "completed"
            target.agent_message = "AUTO_RESUME_COMPLETED"
            target.record_turn_completed()
            target.touch()
            return {"delivery": "reply_started", "previous_result": {}}

        monkeypatch.setattr(backend, "send_message", resume)
        session_registry.publish(child_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        result = await _wait_with_timeout(manager, 5)
        assert result["status"] == "completed"
        assert result["agent_message"] == "AUTO_RESUME_COMPLETED"
        assert session.turn_seq == 2
        assert "error" not in result
        assert len(prompts) == 1
        assert child_session_id in prompts[0]


@pytest.mark.asyncio
async def test_agents_wait_cli_keeps_target_until_child_termination_and_same_session_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIの実プロセスは子の終了まで待ち、同じsessionが続けた作業の結果を回収する。"""
    isolated_state = tmp_path / "state" / "agent-toolkit"
    monkeypatch.setattr(state_paths, "state_dir", lambda: isolated_state)
    async with _codex_parent_with_child(isolated_state) as (manager, backend, session, writer):
        session.announced = True
        child = "cli-child"
        resumed: list[tuple[str, int]] = []
        process: asyncio.subprocess.Process | None = None

        async def resume(target: state.SessionState, _prompt: str) -> dict[str, Any]:
            # モデルの応答だけを代替し、常駐監視・登録・結果保存・CLI回収は実装を通す。
            state.begin_reply(target)
            resumed.append((target.session_id, target.turn_seq))
            target.status = "completed"
            target.agent_message = "CLI_RESUMED_WORK_COMPLETED"
            target.record_turn_completed()
            target.touch()
            return {"delivery": "reply_started", "previous_result": {}}

        monkeypatch.setattr(backend, "send_message", resume)
        environment = dict(os.environ)
        for name in (
            "AGENT_TOOLKIT_OWNER_SESSION",
            "AGENT_TOOLKIT_DELEGATED_SESSION",
            "AGENT_TOOLKIT_STATUS_HOST_SESSION",
            "CODEX_THREAD_ID",
            "AI_AGENT",
            "CODEX_CI",
            "CLAUDECODE",
            "CURSOR_AGENT",
        ):
            environment.pop(name, None)
        environment.update(
            CLAUDE_CODE_SESSION_ID="root",
            XDG_STATE_HOME=str(isolated_state.parent),
            LOCALAPPDATA=str(isolated_state.parent),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
        )
        cli = pathlib.Path(__file__).with_name("atk.py")
        wait_directory = shared_layout.status_directory("root", isolated_state) / "wait-results" / "root.json"
        try:
            session_registry.publish(child, state=session_registry.PublicationState.PENDING_RUNNING)
            await _complete_codex_turn_with_child(backend, session, child)
            await manager.activate_async()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(cli),
                "agents",
                "wait",
                "--root-session-id",
                "root",
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await _await_state(lambda: (wait_directory / "current.json").is_file() or process.returncode is not None)
            assert process.returncode is None, "子の待機中に公開CLIが終了した"
            current = json.loads((wait_directory / "current.json").read_text(encoding="utf-8"))
            run = json.loads((wait_directory / f"{current['run_id']}.json").read_text(encoding="utf-8"))
            assert run["targets"] == [session.session_id]
            assert session_registry.resolve(child).state is session_registry.Resolution.RUNNING
            assert session.awaiting_auto_resume and not session.result_available
            assert not writer.result_exists(session.session_id)
            await manager._advance_child_session_wait(session)
            await asyncio.sleep(0)
            assert process.returncode is None, "子の未終了中はCLIが待機を続ける"
            assert not resumed

            session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_STATE_TIMEOUT)
            assert process.returncode == 0, stderr.decode()
            rows = [json.loads(line) for line in stdout.decode().splitlines()]
            assert len(rows) == 1
            assert (rows[0]["session_id"], rows[0]["status"], rows[0]["agent_message"]) == (
                session.session_id,
                "completed",
                "CLI_RESUMED_WORK_COMPLETED",
            )
            assert pathlib.Path(rows[0]["agent_message_path"]).read_text(encoding="utf-8") == rows[0]["agent_message"]
            assert "error" not in rows[0]
            assert resumed == [(session.session_id, 2)]
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.communicate()
            await manager.close()


@pytest.mark.asyncio
async def test_codex_kill_during_hold_returns_pending_result_with_unobserved_child(tmp_path: pathlib.Path) -> None:
    """保留中のCodex sessionへの中断は要求を送らず、保留した結果を未観測の孫の識別子とともに確定する。"""
    async with _codex_parent_with_child(tmp_path) as (_manager, backend, session, _writer):
        child_session_id = "codex-child"
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        await _complete_codex_turn_with_child(backend, session, child_session_id)
        await backend.interrupt(session)
        assert session.result_available is True
        assert session.awaiting_auto_resume is False
        assert session.agent_message == f"待機中: {child_session_id}"
        assert session.error == {"unobservedSessions": [child_session_id], "heldResultFinalized": True}


@pytest.mark.asyncio
async def test_codex_auto_resume_failure_reports_unobserved_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """再開の配送に失敗した場合は、待機表明を成功として扱わず、失敗と孫の識別子を返す。"""
    async with _codex_parent_with_child(tmp_path) as (manager, backend, session, _writer):
        child_session_id = "codex-child"
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        await _complete_codex_turn_with_child(backend, session, child_session_id)
        monkeypatch.setattr(backend, "send_message", AsyncMock(side_effect=RuntimeError("resume unavailable")))
        session_registry.publish(child_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        result = await _wait_with_timeout(manager, 5)
        assert result["status"] == "failed"
        assert result["error"]["unobservedSessions"] == [child_session_id]
        assert "resume unavailable" in result["error"]["message"]


@pytest.mark.asyncio
async def test_codex_live_child_survives_notification_deadline(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """子の生存中は期限を過ぎても保留し、終了後に同じ親を再開して最終結果を返す。"""
    async with _codex_parent_with_child(tmp_path) as (manager, backend, session, _writer):
        child_session_id = "codex-child"
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        await _complete_codex_turn_with_child(backend, session, child_session_id)
        session.set_background_deadline(0.0)

        async def resume_and_complete(current: state.SessionState, _prompt: str, **_kwargs: Any) -> dict[str, Any]:
            state.begin_reply(current)
            current.agent_message = "子の成果を反映した最終結果"
            current.status = "completed"
            current.record_turn_completed()
            current.touch()
            return {"delivery": "reply_started"}

        send_message = AsyncMock(side_effect=resume_and_complete)
        monkeypatch.setattr(backend, "send_message", send_message)
        assert (await _wait_with_timeout(manager, 0))["status"] == "running"
        assert session.awaiting_auto_resume
        send_message.assert_not_awaited()
        session_registry.publish(child_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        result = await _wait_with_timeout(manager, 5)
        assert result["status"] == "completed"
        assert result["agent_message"] == "子の成果を反映した最終結果"
        assert "error" not in result
        send_message.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_order", ["before", "during", "after"])
async def test_send_message_preserves_child_wait_until_completion(
    completion_order: str,
    tmp_path: pathlib.Path,
) -> None:
    """Codexへの`send_message`による追送が失敗しても反復しても子sessionの追跡を保ち、保留結果の公開と同じ完了を二重に配送しない。"""
    async with _codex_parent_with_child(tmp_path) as (manager, backend, session, _writer):
        child = "codex-send-child"

        class Client:
            """既存のCodex JSON-RPC契約でthread再開とturn開始の受理を制御する。"""

            closed = False
            reader_failure = None
            fail = True
            turns = 0

            async def request(self, method: str, _params: Any, *, on_sent: Any = None) -> dict[str, Any]:
                if method == "config/read":
                    return {"config": {"model_provider": "openai"}}
                if method == "thread/resume":
                    return {"thread": {"id": session.session_id}}
                assert method == "turn/start"
                if self.fail:
                    raise codex_backend.JsonRpcResponseError(method, -32602, "配送拒否")
                if completion_order == "during":
                    session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
                self.turns += 1
                if on_sent is not None:
                    on_sent()
                return {"turn": {"id": f"turn-{self.turns + 1}"}}

            async def close(self) -> list[str]:
                self.closed = True
                return []

        client = Client()
        backend._clients[session.session_id] = cast(codex_backend.JsonRpcProcess, client)
        session_registry.publish(child, state=session_registry.PublicationState.PENDING_RUNNING)
        await _complete_codex_turn_with_child(backend, session, child)
        failed = await manager.send_message(session.session_id, "追加指示", timeout=1)
        assert failed["delivery"] == "reply_failed"
        assert "previous_result" not in failed
        assert session.awaiting_auto_resume and not session.result_available
        assert session.live_child_session_ids == {child}
        client.fail = False
        if completion_order == "before":
            session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
        for _ in range(2):
            response = await manager.send_message(session.session_id, "追加指示", timeout=1)
            assert response["delivery"] == "reply_started"
            assert "previous_result" not in response
            assert session.live_child_session_ids == {child}
            await backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {"threadId": session.session_id, "turn": {"id": session.turn_id, "status": "completed"}},
                }
            )
            assert session.awaiting_auto_resume
        if completion_order == "after":
            session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        await _await_state(lambda: client.turns == 3)
        await backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session.session_id,
                    "turn": {
                        "id": session.turn_id,
                        "status": "completed",
                        "items": [{"type": "agentMessage", "id": "final", "text": "子の結果を確認した"}],
                    },
                },
            }
        )
        result = await wait_task
        assert result["agent_message"] == "子の結果を確認した"
        assert "error" not in result
        assert client.turns == 3
        assert "agent_message" not in await _wait_with_timeout(manager, 0)
        assert session.result_delivered
