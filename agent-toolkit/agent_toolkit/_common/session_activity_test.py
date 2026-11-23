import concurrent.futures
import contextlib
import dataclasses
import inspect
import json
import os
import pathlib
import subprocess
import threading
from collections.abc import Generator, Iterator
from typing import Any

import psutil
import pytest

from agent_toolkit._common import process_tree, session_activity, session_registry
from agent_toolkit._testing import late_descendant


def _host(engine: str) -> session_activity.HostProcess:
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    return session_activity.HostProcess(engine, identity)


def _transcript(tmp_path: pathlib.Path, engine: str, session_id: str) -> pathlib.Path:
    """2.1.291と0.147.0の記録形は_testing/delegated_threads.pyの採取済み入力にそろえる。"""
    path = tmp_path / f"{session_id}.jsonl"
    records: list[dict[str, Any]] = (
        [{"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": []}}]
        if engine == "claude"
        else [
            {"type": "session_meta", "payload": {"id": session_id}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
    )
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_host_activity(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    host = _host(engine)
    transcript = _transcript(tmp_path, engine, "host-session")
    payload: dict[str, Any] = {"session_id": "host-session", "transcript_path": str(transcript)}

    def emit(event: str, *, agent: str | None = None, output: str = "{}") -> None:
        event_payload = {**payload, "hook_event_name": event}
        if agent is not None:
            event_payload["agent_id"] = agent
        session_activity.record_activity(event_payload, output=output, process=host, state_root=tmp_path)

    def scan() -> session_activity.ActivitySnapshot:
        return session_activity.scan_activity(state_root=tmp_path, processes=())

    emit("UserPromptSubmit")
    assert scan().active_session_ids == {"host-session"}
    emit("Stop", output='{"decision":"block","reason":"再検証"}')
    assert scan().has_activity
    emit("Stop")
    assert not scan().has_activity
    emit("SubagentStart", agent="review")
    assert scan().has_activity
    emit("SubagentStop", agent="review", output='{"decision":"block","reason":"継続"}')
    assert scan().has_activity
    emit("SubagentStop", agent="review")
    assert not scan().has_activity
    emit("UserPromptSubmit")
    assert scan().has_activity
    emit("SessionEnd")
    assert not scan().has_activity
    assert not (session_activity.activity_directory(tmp_path) / "host-session.json").exists()


def test_stop_does_not_replace_host_turn_completion(tmp_path: pathlib.Path) -> None:
    path = _transcript(tmp_path, "codex", "continued")
    payload = {"session_id": "continued", "transcript_path": str(path), "hook_event_name": "Stop"}
    session_activity.record_activity(payload, process=_host("codex"), state_root=tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    assert session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
def test_living_roots_include_waiting_but_preserve_activity(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    transcript = _transcript(tmp_path, engine, "root")
    for event in ("SessionStart", "Stop"):
        session_activity.record_activity(
            {"session_id": "root", "hook_event_name": event, "transcript_path": str(transcript)},
            process=_host(engine),
            state_root=tmp_path,
        )
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root")
    session_activity.record_activity(
        {"session_id": "child", "hook_event_name": "SessionStart"},
        process=_host(engine),
        state_root=tmp_path,
    )
    before = {path: path.read_bytes() for path in session_activity.activity_directory(tmp_path).iterdir()}
    activity = session_activity.scan_activity(state_root=tmp_path, processes=())
    assert session_activity.living_root_session_ids(state_root=tmp_path) == {"root"}
    assert session_activity.root_activity("root", state_root=tmp_path)["status"] == "稼働中"
    assert session_activity.root_activity("missing", state_root=tmp_path)["status"] == "不明"
    assert session_activity.scan_activity(state_root=tmp_path, processes=()) == activity
    assert "root" not in activity.active_session_ids
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("invalid", ["broken", "mismatched", "missing-process", "reused", "dead"])
def test_living_roots_require_valid_process_evidence(tmp_path: pathlib.Path, invalid: str) -> None:
    session_activity.record_activity(
        {"session_id": "root", "hook_event_name": "SessionStart"},
        process=_host("codex"),
        state_root=tmp_path,
    )
    path = session_activity.activity_directory(tmp_path) / "root.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if invalid == "mismatched":
        record["session_id"] = "other"
    elif invalid == "missing-process":
        record.pop("process")
    elif invalid == "reused":
        record["process"]["start_id"] = "other-generation"
    elif invalid == "dead":
        record["process"] = {"pid": 2147483647, "create_time": 1}
    path.write_text("{" if invalid == "broken" else json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    assert not session_activity.living_root_session_ids(state_root=tmp_path)
    expected = "終了" if invalid == "dead" else "不明"
    assert session_activity.root_activity("root", state_root=tmp_path)["status"] == expected
    assert path.read_bytes() == before


@pytest.mark.parametrize("variant", ["live", "dead", "reused", "missing-process", "broken"])
def test_sweep_removes_only_records_of_exited_processes(tmp_path: pathlib.Path, variant: str) -> None:
    session_activity.record_activity(
        {"session_id": "root", "hook_event_name": "SessionStart"},
        process=_host("codex"),
        state_root=tmp_path,
    )
    path = session_activity.activity_directory(tmp_path) / "root.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if variant == "dead":
        record["process"] = {"pid": 2147483647, "create_time": 1}
    elif variant == "reused":
        record["process"]["start_id"] = "other-generation"
    elif variant == "missing-process":
        record.pop("process")
    path.write_text("{" if variant == "broken" else json.dumps(record), encoding="utf-8")
    exited = variant in {"dead", "reused"}
    assert session_activity.sweep_exited_records(state_root=tmp_path) == int(exited)
    assert path.exists() is not exited


def test_dead_and_reused_pid_records_expire(tmp_path: pathlib.Path) -> None:
    host = _host("codex")
    reused = dataclasses.replace(host.identity, create_time=host.identity.create_time - 1, start_id="other-generation")
    dead = session_registry.ProcessIdentity(2147483647, 1)
    for session_id, identity in (("reused", reused), ("dead", dead)):
        session_activity.record_activity(
            {"session_id": session_id, "hook_event_name": "UserPromptSubmit"},
            process=session_activity.HostProcess("codex", identity),
            state_root=tmp_path,
        )
    assert not session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


def test_unreadable_record_is_activity_candidate(tmp_path: pathlib.Path) -> None:
    directory = session_activity.activity_directory(tmp_path)
    directory.mkdir()
    (directory / "broken.json").write_text("{", encoding="utf-8")
    unreadable = session_activity.scan_activity(state_root=tmp_path, processes=())
    assert unreadable.uncertain and unreadable.active_session_ids == {"broken"}
    assert not session_activity.scan_activity(
        state_root=tmp_path,
        processes=(),
        excluded_session_ids={"broken"},
    ).has_activity


def test_loop_descendants_and_other_loop(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    for session_id, parent in (("loop", None), ("child", "loop"), ("grandchild", "child"), ("other", None)):
        if parent:
            monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", parent)
        else:
            monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
        session_activity.record_activity(
            {"session_id": session_id, "hook_event_name": "UserPromptSubmit"},
            process=_host("codex"),
            state_root=tmp_path,
        )
    snapshot = session_activity.scan_activity(state_root=tmp_path, processes=(), excluded_session_ids={"loop"})
    assert snapshot.active_session_ids == {"other"}
    assert not session_activity.scan_activity(
        state_root=tmp_path,
        processes=(),
        excluded_pids={os.getpid()},
    ).has_activity


def test_registry_active_child_and_terminal(tmp_path: pathlib.Path) -> None:
    session_registry.publish(
        "child", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="loop", state_root=tmp_path
    )
    assert session_activity.scan_activity(state_root=tmp_path, processes=()).active_session_ids == {"child"}
    assert not session_activity.scan_activity(
        state_root=tmp_path,
        processes=(),
        excluded_session_ids={"loop"},
    ).has_activity
    session_registry.publish("child", state=session_registry.PublicationState.RESULT_COMPLETED, state_root=tmp_path)
    assert not session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


def test_parallel_child_hooks_keep_each_actor(tmp_path: pathlib.Path) -> None:
    host = _host("codex")

    def emit(index: int) -> None:
        session_activity.record_activity(
            {"session_id": "parallel", "agent_id": f"agent-{index}", "hook_event_name": "SubagentStart"},
            process=host,
            state_root=tmp_path,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(emit, range(12)))
    record = json.loads((session_activity.activity_directory(tmp_path) / "parallel.json").read_text(encoding="utf-8"))
    assert len(record["actors"]) == 12
    assert len(list(session_activity.activity_directory(tmp_path).glob("*.json"))) == 1


@pytest.mark.parametrize("event", ["PreToolUse", "Stop"])
def test_other_session_records_while_host_probe_blocks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    """会話Aのホスト祖先探索が止まっている間も、同じ状態領域の会話Bの記録は共有ロックを待たずに完了する。"""
    probing = threading.Event()
    release = threading.Event()
    host = _host("claude")

    def blocking_probe() -> session_activity.HostProcess:
        probing.set()
        assert release.wait(10)
        return host

    monkeypatch.setattr(session_activity, "host_process", blocking_probe)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            session_activity.record_activity,
            {"session_id": "probing", "hook_event_name": "PreToolUse"},
            state_root=tmp_path,
        )
        try:
            assert probing.wait(10)
            other = pool.submit(
                session_activity.record_activity,
                {"session_id": "other", "hook_event_name": event},
                process=host,
                state_root=tmp_path,
            )
            other.result(timeout=5)
            assert not first.done()
        finally:
            release.set()
        first.result(timeout=10)
    directory = session_activity.activity_directory(tmp_path)
    assert json.loads((directory / "other.json").read_text(encoding="utf-8"))["actors"]["main"] in {"active", "waiting"}
    assert json.loads((directory / "probing.json").read_text(encoding="utf-8"))["actors"]["main"] == "active"


def test_session_end_skips_host_probe(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_activity.record_activity(
        {"session_id": "ending", "hook_event_name": "PreToolUse"}, process=_host("claude"), state_root=tmp_path
    )

    def unexpected_probe() -> session_activity.HostProcess:
        raise AssertionError("SessionEndで祖先を探索した")

    monkeypatch.setattr(session_activity, "host_process", unexpected_probe)
    session_activity.record_activity({"session_id": "ending", "hook_event_name": "SessionEnd"}, state_root=tmp_path)
    assert not (session_activity.activity_directory(tmp_path) / "ending.json").exists()


@pytest.mark.parametrize("replacement", [{"actors": {"main": []}}, {"engine": []}, {"version": True}])
def test_invalid_schema_is_not_waiting(tmp_path: pathlib.Path, replacement: dict[str, Any]) -> None:
    transcript = _transcript(tmp_path, "codex", "invalid")
    session_activity.record_activity(
        {"session_id": "invalid", "hook_event_name": "Stop", "transcript_path": str(transcript)},
        process=_host("codex"),
        state_root=tmp_path,
    )
    path = session_activity.activity_directory(tmp_path) / "invalid.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, **replacement}), encoding="utf-8")
    snapshot = session_activity.scan_activity(state_root=tmp_path, processes=())
    assert snapshot.uncertain and snapshot.active_session_ids == {"invalid"}


def test_codex_parent_precedes_inherited_owner(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "native-child.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": "child",
                    "source": {"subagent": {"thread_spawn": {"parent_thread_id": "loop", "depth": 1}}},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "old")
    session_activity.record_activity(
        {"session_id": "child", "hook_event_name": "UserPromptSubmit", "transcript_path": str(transcript)},
        process=_host("codex"),
        state_root=tmp_path,
    )
    assert not session_activity.scan_activity(
        state_root=tmp_path,
        processes=(),
        excluded_session_ids={"loop"},
    ).has_activity


def test_registered_host_of_excluded_session_is_not_unknown(tmp_path: pathlib.Path) -> None:
    host = _host("codex")
    session_registry.publish(
        "own",
        state=session_registry.PublicationState.PENDING_RUNNING,
        processes=[host.identity],
        launcher_session_id="loop",
        state_root=tmp_path,
    )
    assert not session_activity.scan_activity(
        state_root=tmp_path,
        processes=[host],
        excluded_session_ids={"loop"},
    ).has_activity


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_host_completion_without_stop_hook(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str) -> None:
    monkeypatch.setattr(session_activity.time, "time", lambda: 100.0)
    transcript = _transcript(tmp_path, engine, "no-stop")
    payload = {"session_id": "no-stop", "hook_event_name": "UserPromptSubmit", "transcript_path": str(transcript)}
    host = _host(engine)
    session_activity.record_activity(payload, process=host, state_root=tmp_path)
    completed = (
        {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": []}}
        if engine == "claude"
        else {"type": "event_msg", "payload": {"type": "task_complete"}}
    )
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({**completed, "timestamp": "1970-01-01T00:03:20+00:00"}) + "\n")
    assert not session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity
    monkeypatch.setattr(session_activity.time, "time", lambda: 300.0)
    session_activity.record_activity(payload, process=host, state_root=tmp_path)
    assert session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


def test_open_codex_record_without_hook(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "rollout-native.jsonl"
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "native"}})
        + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}})
        + "\n",
        encoding="utf-8",
    )
    host = _host("codex")
    with path.open("a+", encoding="utf-8") as transcript:
        assert not session_activity.scan_activity(state_root=tmp_path, processes=[host]).has_activity
        transcript.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
        transcript.flush()
        assert session_activity.scan_activity(state_root=tmp_path, processes=[host]).active_session_ids == {"native"}


@pytest.fixture(name="daemons")
def _daemons(tmp_path: pathlib.Path) -> Iterator[list[session_activity.HostProcess]]:
    with contextlib.ExitStack() as stack:
        processes = [stack.enter_context(late_descendant.held_process(tmp_path)) for _ in range(2)]
        hosts = []
        for process in processes:
            identity = session_registry.ProcessIdentity.of(process.pid)
            assert identity is not None
            hosts.append(session_activity.HostProcess("codex", identity))
        yield hosts


@pytest.mark.parametrize("stage", ["acquire-second", "body", "release-one"])
def test_daemons_reclaims_every_acquired_process_on_failure(
    monkeypatch: pytest.MonkeyPatch, stage: str, tmp_path: pathlib.Path
) -> None:
    """2件目の獲得・利用・前段の解放で失敗しても、獲得済み全件の終了を確認する。"""
    acquired: list[subprocess.Popen[Any]] = []
    real_scope = process_tree.process_scope
    real_stop = process_tree.stop_owned_process

    @contextlib.contextmanager
    def scope(*args: Any, **kwargs: Any) -> Generator[subprocess.Popen[Any]]:
        if stage == "acquire-second" and acquired:
            raise RuntimeError("2件目の生成失敗")
        with real_scope(*args, **kwargs) as process:
            acquired.append(process)
            yield process

    def stop(process: subprocess.Popen[Any], **kwargs: Any) -> process_tree.ReclaimError | None:
        result = real_stop(process, **kwargs)
        if stage == "release-one" and process is acquired[-1]:
            raise RuntimeError("前段の解放失敗")
        return result

    monkeypatch.setattr(process_tree, "process_scope", scope)
    monkeypatch.setattr(process_tree, "stop_owned_process", stop)
    lifetime = inspect.unwrap(_daemons)(tmp_path)
    if stage == "acquire-second":
        with pytest.raises(RuntimeError, match="2件目の生成失敗"):
            next(lifetime)
    else:
        next(lifetime)
        if stage == "body":
            with pytest.raises(AssertionError, match="利用中の失敗"):
                lifetime.throw(AssertionError("利用中の失敗"))
        else:
            with pytest.raises(RuntimeError, match="前段の解放失敗"):
                lifetime.close()
    assert len(acquired) == (1 if stage == "acquire-second" else 2)
    assert all(process.poll() is not None for process in acquired)


def test_review_activity_limits_to_target_and_descendants(tmp_path, monkeypatch) -> None:

    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    parent = _transcript(tmp_path, "claude", "root")
    session_activity.record_activity(
        {"hook_event_name": "Stop", "session_id": "root", "transcript_path": str(parent)},
        process=_host("claude"),
        state_root=tmp_path,
    )
    assert not session_activity.review_activity({"root"}, state_root=tmp_path).has_activity
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root")
    session_activity.record_activity(
        {"hook_event_name": "SessionStart", "session_id": "child"}, process=_host("claude"), state_root=tmp_path
    )
    assert session_activity.review_activity({"root"}, state_root=tmp_path).has_activity
    assert not session_activity.review_activity({"other"}, state_root=tmp_path).has_activity


def test_host_processes_reads_current_generation_despite_cached_previous_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    pid = os.getpid()
    current = session_registry.ProcessIdentity.of(pid)
    assert current is not None

    stale = psutil.Process(pid)
    monkeypatch.setattr(stale, "_create_time", current.create_time - 10000)
    monkeypatch.setattr(stale, "_ident", (pid, current.create_time - 10000))
    monkeypatch.setattr(stale, "_exe", "/previous-generation/codex")
    monkeypatch.setitem(vars(psutil)["_pmap"], pid, stale)
    real_process_iter = psutil.process_iter

    def own_process(*args: Any, **kwargs: Any) -> list[psutil.Process]:
        return [item for item in real_process_iter(*args, **kwargs) if item.pid == pid]

    monkeypatch.setattr(psutil, "process_iter", own_process)

    assert not session_activity.host_processes()

    monkeypatch.setattr(psutil.Process, "name", lambda self: "claude")
    hosts = session_activity.host_processes()
    assert hosts == (session_activity.HostProcess("claude", current),)
    assert hosts[0].identity.exists()
