"""CLI要求から所有MCPへの適用、認可、1回の自己再開を検証する。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from agent_toolkit._agents_server import effort_control
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import effort_requests, session_layout, session_registry
from agent_toolkit._testing.agents_server_support import FakeBackend, install_backend


@pytest.mark.parametrize("engine", ["agy", "opencode"])
@pytest.mark.parametrize("initially_visible", [True, False])
@pytest.mark.asyncio
async def test_cli_without_conversation_environment_resolves_only_own_executor(engine, initially_visible, monkeypatch):
    """同じルートでも実行プロセスの世代が一致する会話だけを本人として選ぶ。"""
    session = _session()
    session.engine = engine
    session.attach_executor(os.getpid())
    session.publish_registration()
    directory = session_layout.status_directory("test-root")
    directory.mkdir(parents=True)
    projection = directory / "root.json"
    visible = json.dumps({"sessions": [{"session_id": "child"}]})
    projection.write_text(visible if initially_visible else '{"sessions": []}', encoding="utf-8")
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "test-root", "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}
    monkeypatch.setattr(effort_control.effort, "cli_levels", lambda *_args, **_kwargs: ("low", "medium", "high"))
    waiting = threading.Event()
    sleep = time.sleep

    def observe_wait(seconds):
        waiting.set()
        sleep(seconds)

    monkeypatch.setattr(effort_requests.time, "sleep", observe_wait)
    request = asyncio.create_task(asyncio.to_thread(effort_requests.request, "up", None, environment))
    try:
        async with asyncio.timeout(5):
            if not initially_visible:
                assert await asyncio.to_thread(waiting.wait, 3)
                assert not await asyncio.to_thread(effort_requests.pending)
                projection.write_text(visible, encoding="utf-8")
            while not await asyncio.to_thread(effort_requests.pending):
                await asyncio.sleep(0)
            await effort_control.process_requests({"child": session}, _catalog, _apply)
            response = await asyncio.shield(request)
        assert response["session_id"] == "child" and response["effort"] == "medium"
        assert session.effort_history[-1][-1] == "child"
    finally:
        await asyncio.gather(request, return_exceptions=True)
    other = SessionState("other", "/work", engine=engine, model_type="review", publish_registry=True)
    other.attach_executor(os.getpid())
    other.publish_registration()
    (directory / "root.json").write_text(
        json.dumps({"sessions": [{"session_id": "child"}, {"session_id": "other"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="呼出元の会話ID"):
        effort_requests.request("up", None, environment)
    with pytest.raises(ValueError, match="呼出元の会話ID"):
        effort_requests.request("up", None, {"AGENT_TOOLKIT_OWNER_SESSION": "unrelated-root"})


def _session():
    session_registry.declare_effort_receiver()
    session = SessionState(session_id="child", cwd="/work", model="test-model", effort="low", model_type="review", turn_seq=1)
    session.publish_registry = True
    session.launcher_session_id = "parent"
    session.publish_registration()
    return session


async def _catalog(_engine):
    return [
        {
            "model": "test-model",
            "supportedReasoningEfforts": [{"reasoningEffort": value} for value in ("low", "medium", "high")],
        }
    ]


async def _apply(_session, _value):
    return None


@pytest.mark.parametrize("actor", ["parent", "child"])
@pytest.mark.asyncio
async def test_cli_request_applies_one_step_and_persists_history(actor):
    session = _session()
    task = asyncio.create_task(asyncio.to_thread(effort_requests.request, "up", "child", {"CODEX_THREAD_ID": actor}))
    try:
        async with asyncio.timeout(5):
            while not await asyncio.to_thread(effort_requests.pending):
                await asyncio.sleep(0)
            await effort_control.process_requests({"child": session}, _catalog, _apply)
            result = await asyncio.shield(task)
        assert result["effort"] == "medium" and result["changed"] is True
        assert result["auto_resume"] is (actor == "child")
        assert session.effort_resume_turn == (1 if actor == "child" else None)
        info = session_registry.resolve("child").resume_info
        assert info is not None and info.effort == "medium"
        assert info.launch_info.effort_history[0][1:] == ("low", "medium", actor)
        async with AgentsServerManager(status_writer=None) as manager:
            manager.sessions[session.session_id] = session
            listed = await manager.list_sessions(include_terminated=True)
            public = next(item for item in listed["sessions"] if item["session_id"] == session.session_id)
            assert (public["engine"], public["model"], public["effort"]) == ("codex", "test-model", "medium")
    finally:
        # to_threadの取消だけでは同期CLIは止まらないため、有限期限の終了まで回収する。
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("actor", ["sibling", "grandparent"])
def test_cli_refuses_nonowners_before_writing_request(actor):
    _session()
    with pytest.raises(ValueError, match="直接の親"):
        effort_requests.request("up", "child", {"CODEX_THREAD_ID": actor})
    assert not list(session_registry.registry_directory().glob("*.effort-request"))


@pytest.mark.parametrize(
    ("launch_kind", "model_type"),
    [("explore", "chores"), ("shell", "chores"), ("write", "write"), ("delegate", "orchestrate")],
)
@pytest.mark.asyncio
async def test_owner_revalidates_launch_scope(launch_kind, model_type):
    session = _session()
    session.launch_kind, session.model_type = launch_kind, model_type
    owner = session_registry.owner_process()
    assert owner is not None
    path = session_registry.registry_directory() / "scope.effort-request"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "session_id": "child",
                "actor": "parent",
                "direction": "up",
                "owner": owner.payload(),
                "deadline": time.monotonic() + 30,
            }
        ),
        encoding="utf-8",
    )
    await effort_control.process_requests({"child": session}, _catalog, _apply)
    result = json.loads(path.with_suffix(".effort-response").read_text(encoding="utf-8"))
    assert "task/delegate" in result["error"]
    assert session.effort == "low" and not session.effort_history


@pytest.mark.asyncio
async def test_apply_exception_is_request_error_and_does_not_change_effort():
    session = _session()
    owner = session_registry.owner_process()
    assert owner is not None
    path = session_registry.registry_directory() / "failure.effort-request"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "session_id": "child",
                "actor": "parent",
                "direction": "up",
                "owner": owner.payload(),
                "deadline": time.monotonic() + 30,
            }
        ),
        encoding="utf-8",
    )

    async def fail(_session, _value):
        raise LookupError("SDK control refused")

    await effort_control.process_requests({"child": session}, _catalog, fail)
    result = json.loads(path.with_suffix(".effort-response").read_text(encoding="utf-8"))
    assert "SDK control refused" in result["error"]
    assert session.effort == "low" and not session.effort_history


@pytest.mark.asyncio
async def test_self_change_resumes_only_once_after_turn_end():
    session = _session()
    session.effort_resume_turn = session.turn_seq
    sent = []

    async def send(current, body):
        assert not current.result_available
        sent.append((current.session_id, body))
        return {"delivery": "reply_started"}

    await effort_control.resume_changed({"child": session}, send)
    assert not sent
    session.status = "completed"
    session.record_turn_completed()
    assert not session.result_available
    await effort_control.resume_changed({"child": session}, send)
    await effort_control.resume_changed({"child": session}, send)
    assert len(sent) == 1 and sent[0][0] == "child"
    assert session.effort_resume_turn is None


@pytest.mark.asyncio
async def test_independent_next_turn_consumes_self_resume_reservation():
    session = _session()
    session.effort_resume_turn = 0

    async def unexpected(_session, _prompt):
        raise AssertionError("別経路で開始したturnへ重複送信した")

    await effort_control.resume_changed({"child": session}, unexpected)
    assert session.effort_resume_turn is None


@pytest.mark.parametrize("fault", ["missing", "version", "owner", "generation"])
def test_cli_refuses_unverified_receiver_without_saving(fault):
    """未宣言・別世代では会話を保持し、要求を送らない。"""
    _session()
    path = session_registry.registry_directory() / "child.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if fault == "missing":
        payload.pop("effort_receiver")
    elif fault == "version":
        payload["effort_receiver"]["version"] = 2
    elif fault == "owner":
        payload["effort_receiver"]["process"]["pid"] += 1
    else:
        payload["processes"][0]["create_time"] += 1
        payload["processes"][0].pop("start_id", None)
        payload["effort_receiver"]["process"] = payload["processes"][0]
    before = json.dumps(payload)
    path.write_text(before, encoding="utf-8")
    result = effort_requests.request("up", "child", {"CODEX_THREAD_ID": "parent"})
    assert result["request_sent"] is False and result["error"] and result["next_action"]
    assert path.read_text(encoding="utf-8") == before
    assert not list(path.parent.glob("*.effort-request"))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="forkを提供するOSだけで親宣言の継承を確認する")
def test_fork_does_not_publish_parent_receiver_declaration(tmp_path):
    # pytest workerの監視スレッドとロックを継承しない、新しいPythonで実forkする。
    sample = tmp_path / "fork_sample.py"
    sample.write_text(
        """import os
import pathlib
import sys
import threading
from agent_toolkit._common import session_registry

state_root = pathlib.Path(sys.argv[1])
assert threading.active_count() == 1
session_registry.declare_effort_receiver()
pid = os.fork()
if pid == 0:
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish(
        "fork-child", state=session_registry.PublicationState.RESULT_COMPLETED,
        processes=[owner], state_root=state_root,
    )
    os._exit(0)
waited, status = os.waitpid(pid, 0)
assert waited == pid and os.waitstatus_to_exitcode(status) == 0
print(pid)
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", str(sample), str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    pid = int(result.stdout.strip())
    payload = json.loads((session_registry.registry_directory(tmp_path) / "fork-child.json").read_text(encoding="utf-8"))
    assert payload["processes"][0]["pid"] == pid
    assert "effort_receiver" not in payload


@pytest.mark.asyncio
async def test_completed_child_parent_change_is_used_by_next_turn(monkeypatch):
    session = _session()
    session.status = "completed"
    session.record_turn_completed()
    session.publish_registration()
    async with AgentsServerManager(status_writer=None) as manager:
        manager.sessions[session.session_id] = session
        monkeypatch.setattr(manager, "_effort_catalog", _catalog)
        manager.activate()
        result = await asyncio.to_thread(effort_requests.request, "up", "child", {"CODEX_THREAD_ID": "parent"})
        assert result["effort"] == "medium" and result["applies"] == "next_turn"
        assert session.status == "completed" and session.effort_resume_turn is None
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        await manager.send_message("child", "続けよ")
        resumed = manager.sessions["child"]
        assert resumed.session_id == "child" and resumed.effort == "medium" and resumed.turn_seq == 2
        info = session_registry.resolve("child").resume_info
        assert info is not None and info.effort == "medium"
