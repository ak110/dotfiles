"""活動の生成と開始判定へ渡す読取結果を、隔離した会話と実PIDで検証する。"""

import concurrent.futures
import dataclasses
import json
import os
import pathlib
from typing import Any

import pytest

from agent_toolkit._common import session_activity, session_registry


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
    """入力、Stop継続、実ターン終了、子の稼働、新入力と終了を両ホストで判定する。"""
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
    """Stop許可の後にホストがターンを続けた場合は、次の入力hookが欠けても活動へ戻る。"""
    path = _transcript(tmp_path, "codex", "continued")
    payload = {"session_id": "continued", "transcript_path": str(path), "hook_event_name": "Stop"}
    session_activity.record_activity(payload, process=_host("codex"), state_root=tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
    assert session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


def test_dead_and_reused_pid_records_expire(tmp_path: pathlib.Path) -> None:
    """保存記録のactiveだけでは活動とせず、PID再利用と実プロセスの終了を除く。"""
    host = _host("codex")
    reused = dataclasses.replace(host.identity, create_time=host.identity.create_time - 1)
    dead = session_registry.ProcessIdentity(2147483647, 1)
    for session_id, identity in (("reused", reused), ("dead", dead)):
        session_activity.record_activity(
            {"session_id": session_id, "hook_event_name": "UserPromptSubmit"},
            process=session_activity.HostProcess("codex", identity),
            state_root=tmp_path,
        )
    assert not session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


def test_unknown_and_unreadable_are_activity_candidates(tmp_path: pathlib.Path) -> None:
    """実在する未登録ホストと破損記録を、無活動へ変換しない。"""
    unknown = session_activity.scan_activity(state_root=tmp_path, processes=[_host("codex")])
    assert unknown.has_activity and unknown.uncertain
    assert any("未登録" in reason for reason in unknown.reasons)
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
    """共通の環境印を持つ別loopを数え、自loopの会話・孫だけを除外する。"""
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
    """hookがない委譲先も登録簿の親子と終端を使って判定する。"""
    session_registry.publish("child", terminal=False, launcher_session_id="loop", state_root=tmp_path)
    assert session_activity.scan_activity(state_root=tmp_path, processes=()).active_session_ids == {"child"}
    assert not session_activity.scan_activity(
        state_root=tmp_path,
        processes=(),
        excluded_session_ids={"loop"},
    ).has_activity
    session_registry.publish("child", terminal=True, state_root=tmp_path)
    assert not session_activity.scan_activity(state_root=tmp_path, processes=()).has_activity


def test_parallel_child_hooks_keep_each_actor(tmp_path: pathlib.Path) -> None:
    """並行する子hookの上書きで他の稼働中の子を失わず、記録を履歴として増やさない。"""
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


@pytest.mark.parametrize("replacement", [{"actors": {"main": []}}, {"engine": []}, {"version": True}])
def test_invalid_schema_is_not_waiting(tmp_path: pathlib.Path, replacement: dict[str, Any]) -> None:
    """ターン終了記録があっても、活動JSONの形式を読めない場合は無活動にしない。"""
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
    """実threadの親を古い環境値で上書きせず、活動記録のない自loopの子も除外する。"""
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
    """登録済みの自会話を除外した後、同じPIDを未登録候補へ再追加しない。"""
    host = _host("codex")
    session_registry.publish(
        "own",
        terminal=False,
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
    """Stop記録の欠落を実終了で補い、次の入力では古い終了を使わない。"""
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
    """hookがない生存ホストでも、実際に開くrolloutの終了と次の開始を取得する。"""
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
