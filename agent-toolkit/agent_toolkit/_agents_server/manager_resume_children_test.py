"""`_agents_server/manager_resume.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import datetime
import json
import logging
import pathlib
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import resume_waits, shared_layout, shared_roots, state, status_file, wait_output_tracking
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    ThreadReadBackend,
    _complete,
    _install_backend,
    _manager_with_fake,
    _publish_orphaned_codex,
    _publish_recovered_session,
    _show_through_tool,
    _use_real_plugin_preflight,
    _write_failing_uv,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_auto_resume_delivery_failure_terminates_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """自動再開の配送失敗を保留本文と復旧情報付きの失敗として返す。"""
    _publish_recovered_session(monkeypatch, tmp_path, "child-terminal", "completed")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
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
        assert backend.send_calls == 1
        await manager.close()


@contextlib.asynccontextmanager
async def _delegate_with_child_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, FakeBackend, state.SessionState, status_file.StatusFileWriter]]:
    """委譲先1件と、その委譲先が別プロセスのMCPサーバーで子sessionを起動する書込主体を用意する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("claude", "model", "high")]
    )
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
        started = await manager.start("execute", "委譲する", str(tmp_path))
        child_writer = status_file.StatusFileWriter(
            {},
            shared_roots.StatusFileIdentity("root-session", "delegate-host.json", "delegate-host"),
            state_root=tmp_path,
            aggregate_seconds=0,
        )
        child_writer.activate()
        yield manager, backend, manager.sessions[started["session_id"]], child_writer


@pytest.mark.asyncio
async def test_wait_delivers_child_session_result_without_kill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別プロセスが起動した子sessionの終了後、killを用いないwaitが委譲先の結果を配送する。

    子sessionの登録簿レコードが残らない場合も、共有の終了時の結果ファイルから終了を判定する。
    """
    async with _delegate_with_child_writer(monkeypatch, tmp_path) as (manager, backend, delegate, child_writer):
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
        # 所有側が保持期限で解放した後も、終了時の結果ファイルを第2の根拠に終了と判定する。
        session_registry.release(child.session_id, reason="retention_expired")

        pending = await manager.wait()

        # 子sessionの終了を観測した待機が、killを伴わずに継続指示を1回配送する。
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
async def test_child_collected_early_is_not_redelivered_and_remaining_child_resumes_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """先に終了した子を同じturnの待機で回収した後、残る子の終了で1回だけ再開し、回収済みの子を再び示さない。"""
    async with _delegate_with_child_writer(monkeypatch, tmp_path) as (manager, backend, delegate, child_writer):
        children = {}
        for name in ("child-early", "child-late"):
            child = state.SessionState(name, str(tmp_path), engine="claude", launch_kind="explore")
            child_writer.sessions[name] = child
            children[name] = child
            wait_output_tracking.consume_agents_server_tool_result(
                delegate, "mcp__agents_server__start_explore", {}, {"session_id": name, "status": "running"}
            )
        child_writer.flush()

        # 先に終わった子は、委譲先が同じturnの`atk agents wait`で回収して次の工程へ使う。
        _complete(children["child-early"], message="早い結果")
        child_writer.flush()
        wait_output_tracking.consume_agents_wait_output(
            delegate, json.dumps({"session_id": "child-early", "status": "completed"}) + "\n"
        )
        resume_waits.begin_auto_resume_wait(
            delegate, {"status": "completed", "agent_message": "待機中: child-late", "error": None}
        )

        await manager._advance_child_session_wait(delegate)  # pylint: disable=protected-access
        assert backend.send_calls == 0
        assert delegate.live_child_session_ids == {"child-late"}

        _complete(children["child-late"], message="遅い結果")
        child_writer.flush()
        await manager._advance_child_session_wait(delegate)  # pylint: disable=protected-access
        await manager._advance_child_session_wait(delegate)  # pylint: disable=protected-access

        assert backend.send_calls == 1
        assert "child-late" in backend.prompts[-1]
        assert "child-early" not in backend.prompts[-1]
        assert not delegate.live_child_session_ids
        child_writer.deactivate()
        await manager.close()


@pytest.mark.asyncio
async def test_send_message_resumes_stopped_session(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """破棄後も別保持先の最小状態から同じsessionを暗黙再開する。"""
    async with _manager_with_fake("codex") as (manager, backend):
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
    """終了済みsessionの再開でも、起動コマンドが失敗する作業ディレクトリでは委譲先を再開しない。"""
    async with _manager_with_fake("codex") as (manager, backend):
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
    """登録簿と終了時の結果ファイルが消えた後も、背景待機の出力ファイルに終了行がある孫sessionは未観測にしない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
        parent.live_child_session_ids.add("child-session")
        output = tmp_path / "bqwu17av0.output"
        output.write_text(json.dumps({"session_id": "child-session", "status": status}) + "\n", encoding="utf-8")
        wait_output_tracking.consume_agents_wait_output(
            parent,
            f"Command running in background with ID: bqwu17av0. Output is being written to: {output}. You will be notified.",
        )
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
        manager.sessions[parent.session_id] = parent

        await manager._advance_child_session_wait(parent)  # pylint: disable=protected-access

        error = parent.error if isinstance(parent.error, dict) else {}
        assert "unobservedSessions" not in error
        assert parent.live_child_session_ids == set(expected_unobserved or [])
        await manager.close()


@pytest.mark.asyncio
async def test_child_set_updated_during_resolution_does_not_abort_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """孫sessionの照会中に追跡集合が更新されても、待機の前進が例外で止まらない。

    照会の`await`の間に委譲先の出力の追跡が同じ集合へ孫sessionを加えると、集合を直接回す実装は
    `RuntimeError: Set changed size during iteration`で終わり、待機中の全担当を巻き込む。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
        parent.live_child_session_ids.add("child-a")
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
        manager.sessions[parent.session_id] = parent
        original_resolve = session_registry.resolve

        def resolve_and_track_new_child(session_id: str) -> Any:
            parent.live_child_session_ids.add("child-b")
            return original_resolve(session_id)

        monkeypatch.setattr(session_registry, "resolve", resolve_and_track_new_child)

        await manager._advance_child_session_wait(parent)  # pylint: disable=protected-access

        assert "child-b" in parent.live_child_session_ids
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
        session_registry.publish(
            session_id, state=session_registry.PublicationState.PENDING_RUNNING, engine="claude", cwd=str(tmp_path), turn_seq=3
        )
    async with server_manager.AgentsServerManager(None) as manager:
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
