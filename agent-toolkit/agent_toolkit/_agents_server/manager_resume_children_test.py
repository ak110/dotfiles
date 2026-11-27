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
from anyio.abc import ByteSendStream
from anyio.streams.text import TextSendStream
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, SystemMessage
from claude_agent_sdk._internal.query import Query
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from agent_toolkit._agents_server import (
    agents_wait,
    claude,
    manager_resume,
    result_projection,
    resume_waits,
    retained_results,
    shared_layout,
    shared_roots,
    state,
    status_file,
    wait_output_tracking,
)
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.claude_channels import ChildCompletionDelivery, _CommandChannel, _DeliveryResult
from agent_toolkit._agents_server.claude_plugins import ClaudePluginReloader
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.delegated_session import FINAL_RESPONSE_NOTICE
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
            {"status": "completed", "agent_message": "待機中: child-terminal", "error": None},
        )
        manager.sessions[session.session_id] = session

        async def fail_delivery(_session: state.SessionState, _prompt: str) -> dict[str, Any]:
            backend.send_calls += 1
            raise RuntimeError("delivery failed")

        monkeypatch.setattr(backend, "send_message", fail_delivery)

        response = await manager.wait()

        assert response["status"] == "failed"
        assert response["agent_message"] == "待機中: child-terminal"
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
            shared_roots.StatusFileIdentity("root-session", f"{started['session_id']}.json", started["session_id"]),
            state_root=tmp_path,
            aggregate_seconds=0,
        )
        child_writer.activate()
        yield manager, backend, manager.sessions[started["session_id"]], child_writer


@pytest.mark.asyncio
@pytest.mark.parametrize("child_finished", [False, True])
async def test_wait_delivers_final_body_without_consuming_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, child_finished: bool
) -> None:
    """普通の最終本文を上流へ返しても、子の結果は元の所有者が回収できる。"""
    async with _delegate_with_child_writer(monkeypatch, tmp_path) as (manager, backend, delegate, child_writer):
        child = state.SessionState("grandchild", str(tmp_path), engine="claude", launch_kind="explore")
        child_writer.sessions[child.session_id] = child
        delegate.live_child_session_ids.add(child.session_id)
        if child_finished:
            _complete(child, message="子の結果")
        child_writer.flush()
        resume_waits.begin_auto_resume_wait(delegate, {"status": "completed", "agent_message": "担当の最終結果", "error": None})

        response = await manager.wait()

        assert response["status"] == "completed"
        assert response["agent_message"] == "担当の最終結果"
        assert response["error"] == {"unobservedSessions": [child.session_id]}
        assert backend.send_calls == 0
        if not child_finished:
            _complete(child, message="子の結果")
            child_writer.flush()
        recovered, error = retained_results.take_result(
            "root-session", child.session_id, f"{delegate.session_id}.json", collector="test", state_root=tmp_path
        )
        assert error is None and recovered is not None and recovered["agent_message"] == "子の結果"
        child_writer.deactivate()


@pytest.mark.asyncio
async def test_wait_delivers_child_session_result_without_kill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
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
            {"status": "completed", "agent_message": "待機中: grandchild", "error": None},
        )

        _complete(child, message="子sessionの結果")
        child_writer.flush()
        # 所有側が保持期限で解放した後も、終了時の結果ファイルを第2の根拠に終了と判定する。
        session_registry.release(child.session_id, reason="retention_expired")

        pending = await manager.wait()

        # 子sessionの終了を観測した待機が、killを伴わずに継続指示を1回配送する。
        assert pending["status"] == "running"
        assert backend.send_calls == 1

        assert backend.prompts[-1].count(FINAL_RESPONSE_NOTICE) == 1
        assert (shared_layout.results_directory("root-session", tmp_path) / f"{child.session_id}.json").is_file()

        _complete(delegate, message="子sessionを確認した最終結果")
        response = await manager.wait()

        assert response["status"] == "completed"
        assert response["agent_message"] == "子sessionを確認した最終結果"
        assert "error" not in response
        child_writer.deactivate()
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreground-delivered", "consumed", "published", "old-turn", "other-parent", "legacy"])
async def test_shared_wait_delivery_releases_tracking(monkeypatch, tmp_path, case):
    async with _delegate_with_child_writer(monkeypatch, tmp_path) as (manager, backend, delegate, child_writer):
        child = "grandchild"
        delegate.live_child_session_ids.add(child)
        resume_waits.begin_auto_resume_wait(delegate, {"status": "completed", "agent_message": "担当の最終結果", "error": None})
        session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path), turn_seq=2)
        session_registry.release(child, reason="stopped")
        parent = "different-parent" if case == "other-parent" else delegate.session_id
        directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / f"{parent}.json"
        directory.mkdir(parents=True)
        receipt = {
            "run_id": "wait-run",
            "status": "foreground-delivered" if case in {"old-turn", "other-parent", "legacy"} else case,
            "targets": [child],
            "output": json.dumps({"status": "completed", "session_id": child}),
            "exit_code": 0,
        }
        if case != "legacy":
            receipt["result_turns"] = {child: 1 if case == "old-turn" else 2}
        (directory / "wait-run.json").write_text(json.dumps(receipt), encoding="utf-8")

        response = await manager.wait()
        assert response["status"] == "completed" and response["agent_message"] == "担当の最終結果"
        if case in {"foreground-delivered", "consumed"}:
            assert "error" not in response
        else:
            assert response["error"] == {"unobservedSessions": [child]}
        assert not delegate.live_child_session_ids
        assert backend.send_calls == 0
        child_writer.deactivate()
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_at", ["before_receipt_check", "after_receipt_check", "other_receipt_check"])
async def test_old_wait_delivery_preserves_restarted_child(monkeypatch, tmp_path, restart_at):
    async with _delegate_with_child_writer(monkeypatch, tmp_path) as (manager, backend, delegate, child_writer):
        children = {"child-a", "child-b"}
        for child in children:
            delegate.record_child_activity(child)
        resume_waits.begin_auto_resume_wait(
            delegate, {"status": "completed", "agent_message": "待機中: child-a", "error": None}
        )
        for child in children:
            session_registry.publish(
                child, state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path), turn_seq=2
            )
        directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / f"{delegate.session_id}.json"
        directory.mkdir(parents=True)
        receipt = {
            "run_id": "wait-run",
            "status": "foreground-delivered",
            "targets": sorted(children),
            "output": "",
            "exit_code": 0,
            "result_turns": dict.fromkeys(children, 2),
        }
        (directory / "wait-run.json").write_text(json.dumps(receipt), encoding="utf-8")
        run = manager_resume.run_file_operation
        checks = 0

        async def reenter(function, /, *args, **kwargs):
            nonlocal checks

            def restart() -> None:
                session_registry.publish(
                    "child-a", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(tmp_path), turn_seq=3
                )
                wait_output_tracking.consume_agents_server_tool_result(
                    delegate, "send_message", {"session_id": "child-a"}, {"delivery": "reply_started"}
                )

            checked_child = "child-b" if restart_at == "other_receipt_check" else "child-a"
            observes_child = function is session_registry.resolve and args == (checked_child,)
            checks += observes_child
            at_boundary = observes_child and checks == 2
            if at_boundary and restart_at != "after_receipt_check":
                restart()
            resolution = await run(function, *args, **kwargs)
            if at_boundary and restart_at == "after_receipt_check":
                restart()
            return resolution

        monkeypatch.setattr(manager_resume, "run_file_operation", reenter)
        response = await manager.wait()
        assert response["status"] == "running"
        assert delegate.live_child_session_ids == {"child-a"}
        assert backend.send_calls == 0
        session_registry.publish(
            "child-a", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path), turn_seq=3
        )
        await manager._process_auto_resume(process_controls=False)
        assert backend.send_calls == 1
        assert "終了したsession/thread: child-a\n" in backend.prompts[-1]
        _complete(delegate, message="子の次のターンを回収した最終結果")
        assert (await manager.wait())["agent_message"] == "子の次のターンを回収した最終結果"
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
@pytest.mark.parametrize("first_driver", ["wait", "monitor"])
async def test_wait_and_monitor_send_child_completion_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, first_driver: str
) -> None:
    _publish_recovered_session(monkeypatch, tmp_path, "child-terminal", "completed")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = FakeBackend(manager.sessions, "claude")
        _install_backend(manager, "claude", backend)
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude", turn_seq=2)
        parent.live_child_session_ids.add("child-terminal")
        resume_waits.begin_auto_resume_wait(
            parent, {"status": "completed", "agent_message": "待機中: child-terminal", "error": None}
        )
        manager.sessions[parent.session_id] = parent

        sending = asyncio.Event()
        second_checked = asyncio.Event()
        release = asyncio.Event()
        advance = manager._advance_child_session_wait
        send = manager._send_with_resources

        async def observe_advance(session: state.SessionState) -> None:
            try:
                await advance(session)
            finally:
                task = asyncio.current_task()
                if task is not None and task.get_name() == "second-driver":
                    second_checked.set()

        async def complete_send(session: state.SessionState, prompt: str) -> dict[str, Any]:
            if not sending.is_set():
                sending.set()
                await release.wait()
            delivery = await send(session, prompt)
            if backend.send_calls == 1:
                _complete(session, message="再開後の結果")
            else:
                _complete(session, error={"message": "自動再開が重複した"})
            return delivery

        monkeypatch.setattr(manager, "_advance_child_session_wait", observe_advance)
        monkeypatch.setattr(manager, "_send_with_resources", complete_send)

        async def monitor() -> bool:
            return await manager._process_auto_resume(process_controls=False)

        first, second = (manager.wait, monitor) if first_driver == "wait" else (monitor, manager.wait)
        async with asyncio.timeout(5), asyncio.TaskGroup() as group:
            first_task = group.create_task(first())
            await sending.wait()
            second_task = group.create_task(second(), name="second-driver")
            await second_checked.wait()
            release.set()

        result = first_task.result() if first_driver == "wait" else second_task.result()
        assert backend.send_calls == 1
        assert parent.turn_seq == 3
        assert parent.status == "completed"
        assert isinstance(result, dict)
        if result["status"] == "running":
            result = await manager.wait()
        assert result["status"] == "completed"
        assert result["agent_message"] == "再開後の結果"
        assert "終了したsession/thread: child-terminal" in backend.prompts[-1]


_CHILD_DELIVERY_ORDERS = [
    (cancel, outcome)
    for cancel in ("なし", "取出し前", "書込み前", "書込み中", "書込み後")
    for outcome in ("未送信", "回復不能", "不明", "成功")
    if outcome not in {"未送信", "回復不能"} or cancel not in {"書込み中", "書込み後"}
]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_driver", ["wait", "monitor"])
@pytest.mark.parametrize(("cancel_at", "outcome"), _CHILD_DELIVERY_ORDERS)
async def test_claude_child_completion_orders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, first_driver: str, cancel_at: str, outcome: str
) -> None:
    _publish_recovered_session(monkeypatch, tmp_path, "child-terminal", "completed")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = manager._backend("claude")
        assert isinstance(backend, claude.ClaudeServerManager)
        manager._wait_timeouts["main"] = 0
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude", turn_seq=2)
        parent.cli_turn_state = "idle"
        parent.live_child_session_ids.add("child-terminal")
        body = {"status": "completed", "agent_message": "待機中: child-terminal", "error": None}
        resume_waits.begin_auto_resume_wait(parent, body)
        manager.sessions[parent.session_id] = parent

        paused = asyncio.Event()
        release = asyncio.Event()
        owner_ready = asyncio.Event()
        second_checked = asyncio.Event()
        writes: list[str] = []
        futures: list[asyncio.Future[_DeliveryResult]] = []

        async def pause() -> None:
            if not paused.is_set():
                paused.set()
                await release.wait()

        class Channel(_CommandChannel):
            def send(self, *args: Any, **kwargs: Any) -> asyncio.Future[_DeliveryResult]:
                future = super().send(*args, **kwargs)
                futures.append(future)
                if len(futures) == 1 and cancel_at == "取出し前":
                    paused.set()
                elif len(futures) == 2:
                    second_checked.set()
                return future

        class Reloader(ClaudePluginReloader):
            async def reload_if_idle(self, client: Any, session: state.SessionState | None) -> None:
                del client, session
                if cancel_at == "書込み前":
                    await pause()

        class Stdin(ByteSendStream):
            async def send(self, item: bytes) -> None:
                if cancel_at == "書込み中":
                    await pause()
                writes.append(json.loads(item)["message"]["content"])
                if cancel_at == "書込み後":
                    await pause()
                if outcome == "不明" and len(writes) == 1:
                    raise OSError("合成の書込み異常")

            async def aclose(self) -> None:
                pass

        options = ClaudeAgentOptions(cli_path="synthetic-cli")
        transport = SubprocessCLITransport(prompt="synthetic", options=options)
        client = ClaudeSDKClient(options, transport=transport)
        client._transport = transport
        client._query = Query(transport, is_streaming_mode=True)
        transport._ready = outcome not in {"未送信", "回復不能"}
        transport._stdin_stream = TextSendStream(Stdin())
        channel = Channel()
        backend._channels[parent.session_id] = channel
        reloader = Reloader({}, asyncio.get_running_loop())

        async def own_commands() -> None:
            await owner_ready.wait()
            while True:
                await backend._handle_command(client, parent, await channel.get(), object(), reloader=reloader)

        async def monitor() -> bool:
            return await manager._process_auto_resume(process_controls=False)

        first, second = (manager.wait, monitor) if first_driver == "wait" else (monitor, manager.wait)

        async def run_second() -> dict[str, Any] | bool:
            try:
                return await second()
            finally:
                second_checked.set()

        async with asyncio.timeout(5), asyncio.TaskGroup() as group:
            owner = group.create_task(own_commands())
            replace_during_write = cancel_at == "書込み後" and first_driver == "monitor"
            try:
                if cancel_at != "取出し前":
                    owner_ready.set()
                initial = group.create_task(first())
                overlap = None
                if cancel_at == "なし":
                    await initial
                else:
                    await paused.wait()
                    if cancel_at == "書込み後":
                        assert len(writes) == 1 and parent.turn_start.name == "UNSENT"
                        assert parent.terminal_child_session_ids == {"child-terminal"}
                        if replace_during_write:
                            parent.record_child_activity("child-terminal")
                            parent.live_child_session_ids.remove("child-terminal")
                            parent.record_child_completion("child-terminal")
                            parent.record_child_completion("child-new")
                    elif cancel_at == "書込み中":
                        assert not writes and parent.turn_start.name == "UNSENT"
                    initial.cancel()
                    if cancel_at == "取出し前":
                        with pytest.raises(asyncio.CancelledError):
                            await initial
                    overlap = group.create_task(run_second())
                    await second_checked.wait()
                    owner_ready.set()
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await initial
                    await overlap

                if outcome in {"未送信", "回復不能"}:
                    assert not writes
                    assert parent.pending_result == body
                    assert parent.terminal_child_session_ids == {"child-terminal"}
                    transport._ready = outcome == "未送信"
                    if outcome == "未送信":
                        await second()
                    else:
                        await monitor()

                if outcome == "回復不能":
                    result = await manager.wait()
                    assert result["status"] == "failed"
                    assert result["error"]["delivery"] == "reply_failed"
                    assert result["error"]["unobservedSessions"] == ["child-terminal"]
                    assert await monitor() is False
                    assert not writes
                elif outcome == "不明":
                    if first_driver == "wait" and cancel_at == "なし":
                        result = initial.result()
                    elif first_driver == "monitor" and cancel_at == "取出し前" and overlap is not None:
                        result = overlap.result()
                    else:
                        result = await manager.wait()
                    assert isinstance(result, dict) and result["status"] == "failed"
                    assert result["error"]["delivery"] == "reply_ambiguous"
                    assert result["error"]["unobservedSessions"] == ["child-terminal"]
                    assert "CLIConnectionError" in result["error"]["cause"]
                    action = result["error"]["nextAction"]
                    assert "未再開と確認できた場合に限り" in action and "同じ通知を再送しない" in action
                    assert result_projection.public_result(result)["next_action"] == action
                    assert agents_wait._with_result_next_action(result)["next_action"] == action
                    await monitor()
                    assert len(writes) == 1
                    if replace_during_write:
                        assert parent.terminal_child_session_ids == {"child-terminal", "child-new"}
                else:
                    assert len(writes) == 1
                    assert "終了したsession/thread: child-terminal" in writes[0]
                    assert parent.terminal_child_session_ids == (
                        {"child-terminal", "child-new"} if replace_during_write else set()
                    )
                    assert parent.turn_seq == 3
                    _complete(parent, message="再開後の結果")
                    assert (await manager.wait())["agent_message"] == "再開後の結果"
                    if cancel_at == "書込み前" and first_driver == "monitor":
                        _publish_recovered_session(monkeypatch, tmp_path, "child-next", "completed")
                        session_registry.publish(
                            "child-terminal",
                            state=session_registry.PublicationState.from_status("completed", result_available=True),
                            engine="codex",
                            cwd=str(tmp_path),
                            model="model",
                            effort="high",
                            model_type="execute",
                            turn_seq=3,
                        )
                        parent.live_child_session_ids.update({"child-next", "child-terminal"})
                        resume_waits.begin_auto_resume_wait(
                            parent, {"status": "completed", "agent_message": "待機中: child-next", "error": None}
                        )
                        await monitor()
                        assert len(writes) == 2
                        assert "終了したsession/thread: child-next, child-terminal" in writes[-1]
                        _complete(parent, message="次の結果")
                        assert (await manager.wait())["agent_message"] == "次の結果"
                        assert (await backend.send_message(parent, "通常の追送"))["delivery"] == "reply_started"
                        assert writes[-1] == "通常の追送"
                if replace_during_write:
                    transport._ready = True
                    transport._exit_error = None
                    resume_waits.begin_auto_resume_wait(
                        parent, {"status": "completed", "agent_message": "待機中: child-new", "error": None}
                    )
                    await monitor()
                    assert len(writes) == 2 and "終了したsession/thread: child-new, child-terminal" in writes[-1]
                    assert not parent.terminal_child_session_ids
                    _complete(parent, message="後続の結果")
                    assert (await manager.wait())["agent_message"] == "後続の結果"
            finally:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
                channel.close()
                del backend._channels[parent.session_id]
                await client.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
@pytest.mark.parametrize("old_model_output", [False, True])
@pytest.mark.parametrize("source", ["none", "closed", "eof-wait", "eof-monitor"])
async def test_claude_child_completion_rehomes_only_after_unsent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, write_fails: bool, source: str, old_model_output: bool
) -> None:
    _publish_recovered_session(monkeypatch, tmp_path, "child-terminal", "completed")
    parent_id = "parent-child-rehome"
    writes: list[str] = []
    messages: asyncio.Queue[SystemMessage | ResultMessage] = asyncio.Queue()
    final_result = "状態: completed\n結果: 再開後の結果"

    class Stdin(ByteSendStream):
        async def send(self, item: bytes) -> None:
            writes.append(json.loads(item)["message"]["content"])
            if write_fails:
                raise OSError("書込みの確認を喪失")
            messages.put_nowait(SystemMessage("init", {"session_id": parent_id}))
            messages.put_nowait(ResultMessage("success", 1, 1, False, 1, parent_id, result=final_result))

        async def aclose(self) -> None:
            pass

    class Raw(ClaudeSDKClient):
        async def connect(self, prompt=None) -> None:
            del prompt
            transport = SubprocessCLITransport(prompt="synthetic", options=ClaudeAgentOptions(cli_path="synthetic-cli"))
            transport._stdin_stream = TextSendStream(Stdin())
            transport._ready = True
            self._transport = transport
            self._query = Query(transport, is_streaming_mode=True)

        async def receive_messages(self) -> AsyncGenerator[SystemMessage | ResultMessage]:
            while True:
                yield await messages.get()

        async def get_context_usage(self) -> Any:
            return {}

    async with (
        server_manager.AgentsServerManager(status_writer=None) as manager,
        claude.ClaudeServerManager(manager.sessions, manager._condition, client_factory=Raw) as backend,
        asyncio.timeout(8),
    ):
        _install_backend(manager, "claude", backend)
        parent = state.SessionState(parent_id, str(tmp_path), engine="claude", turn_seq=2)
        parent.cli_turn_state = "idle"
        parent.model_output_observed = old_model_output
        parent.live_child_session_ids.add("child-terminal")
        body = {"status": "completed", "agent_message": "待機中: child-terminal", "error": None}
        resume_waits.begin_auto_resume_wait(parent, body)
        manager.sessions[parent_id] = parent
        if source == "closed":
            channel = _CommandChannel()
            channel.close()
            backend._channels[parent_id] = channel
        if source.startswith("eof-"):
            enqueued = asyncio.Event()

            class Channel(_CommandChannel):
                def send(self, *args: Any, **kwargs: Any) -> asyncio.Future[_DeliveryResult]:
                    future = super().send(*args, **kwargs)
                    enqueued.set()
                    return future

            channel = Channel()
            backend._channels[parent_id] = channel
            manager._wait_timeouts["main"] = 0
            first = asyncio.create_task(
                manager.wait() if source == "eof-wait" else manager._process_auto_resume(process_controls=False)
            )
            await enqueued.wait()
            await backend._receive_idle(parent)
            assert parent.pending_result == body and not writes
            assert await backend._receive_eof(None, parent)
            channel.close()
            await first
        else:
            await manager._process_auto_resume(process_controls=False)
        assert not writes and parent.pending_result == body
        assert parent.terminal_child_session_ids == {"child-terminal"}

        manager._wait_timeouts["main"] = 5
        result = await manager.wait()
        if write_fails:
            assert result["status"] == "failed" and result["error"]["delivery"] == "reply_ambiguous"
            assert "CLIConnectionError" in result["error"]["cause"]
            action = result_projection.public_result(result)["next_action"]
            assert action == result["error"]["nextAction"]
            assert f"atk agents logs {parent_id} --verbose" in action
            assert "記録がないか不十分" in action and "同じ通知を再送しない" in action
        else:
            assert result["status"] == "completed" and result["agent_message"] == final_result
        await manager._process_auto_resume(process_controls=False)
        assert len(writes) == 1 and "終了したsession/thread: child-terminal" in writes[0]
        assert not parent.terminal_child_session_ids


@pytest.mark.parametrize("current_turn", ["started", "completed"])
@pytest.mark.asyncio
async def test_unconfirmed_write_does_not_replace_current_turn_progress(current_turn: str) -> None:
    session = state.SessionState("parent", ".", engine="claude", turn_seq=2)
    resume_waits.begin_auto_resume_wait(session, {"status": "completed", "agent_message": "待機中: child", "error": None})
    session.record_child_completion("child")
    held = resume_waits.HeldTurn.capture(session)
    assert held is not None
    attempt = ChildCompletionDelivery(held, dict(session.claude_child_completion_tokens))
    assert attempt.accept(session)
    state.begin_reply(session, preserve_waits=True)
    attempt.reply_key = (session.turn_seq, session.started_at)
    if current_turn == "started":
        session.record_turn_started()
    else:
        _complete(session, message="このターンで確定済みの結果")
    expected = (session.status, session.agent_message, session.error)

    attempt.outcome = "unconfirmed"
    attempt.fail(session)

    assert (session.status, session.agent_message, session.error) == expected
    assert attempt.settled and not session.terminal_child_session_ids


@pytest.mark.parametrize("replacement_at", ["queued", "accepted", "write", "parent"])
@pytest.mark.parametrize("outcome", ["not_sent", "sent", "unconfirmed"])
@pytest.mark.asyncio
async def test_child_completion_identity_survives_replacement(replacement_at: str, outcome: str) -> None:
    session = state.SessionState("parent", ".", engine="claude", turn_seq=2)
    body = {"status": "completed", "agent_message": "待機中: child", "error": None}
    resume_waits.begin_auto_resume_wait(session, body)
    session.record_child_completion("child")
    held = resume_waits.HeldTurn.capture(session)
    assert held is not None
    older = ChildCompletionDelivery(held, dict(session.claude_child_completion_tokens))
    newer_body = None
    if replacement_at != "queued":
        assert older.accept(session)
    if replacement_at in {"write", "parent"}:
        state.begin_reply(session, preserve_waits=True)
        older.reply_key = (session.turn_seq, session.started_at)
    if replacement_at == "parent":
        newer_body = {**body, "agent_message": "待機中: other-child"}
        resume_waits.begin_auto_resume_wait(session, newer_body)

    wait_output_tracking.consume_agents_server_tool_result(
        session, "send_message", {"session_id": "child"}, {"delivery": "reply_started"}
    )
    wait_output_tracking.consume_agents_server_tool_result(session, "kill", {"session_id": "child"}, {"status": "completed"})
    session.record_child_completion("other-child")
    newer = dict(session.claude_child_completion_tokens)
    assert older.children["child"] != newer["child"]
    if replacement_at == "queued":
        assert not older.accept(session)
    elif replacement_at == "accepted":
        assert not older.available(session)
    elif outcome == "not_sent" and replacement_at != "parent":
        held.restore(session)
        assert not older.available(session)
    elif outcome == "sent":
        older.consume(session)
    elif outcome == "unconfirmed":
        assert outcome == "unconfirmed"
        older.outcome = "unconfirmed"
        older.fail(session)
        if replacement_at != "parent":
            assert session.error["unobservedSessions"] == ["child"]
    if newer_body is not None:
        assert session.pending_result == newer_body
    assert session.terminal_child_session_ids == {"child", "other-child"}
    assert session.claude_child_completion_tokens == newer


@pytest.mark.asyncio
async def test_send_message_resumes_stopped_session(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
async def test_resume_reports_removed_cwd_instead_of_missing_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    worktree = tmp_path / "removed-worktree"
    worktree.mkdir()
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("saved-session", str(worktree), engine="codex", turn_seq=1)
        _complete(session, message="前のturn")
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[session.session_id] = session
        worktree.rmdir()
        _use_real_plugin_preflight(monkeypatch)

        with pytest.raises(ValueError) as raised:
            await manager.send_message("saved-session", "続行")

        assert str(raised.value) == f"委譲先の作業ディレクトリが存在しないため委譲先を起動しない: cwd={worktree}"
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
        resume_waits.begin_auto_resume_wait(
            parent, {"status": "completed", "agent_message": "待機中: child-session", "error": None}
        )
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
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "待機中: child-a", "error": None})
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
