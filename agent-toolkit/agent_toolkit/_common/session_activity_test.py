"""活動の生成と開始判定へ渡す読取結果を、隔離した会話と実PIDで検証する。"""

import concurrent.futures
import contextlib
import dataclasses
import inspect
import json
import os
import pathlib
import subprocess
import sys
import threading
from collections.abc import Generator, Iterator
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._common import process_tree, session_activity, session_registry, state_paths
from agent_toolkit._testing.git_repository import init_repository, run_git

_TARGET = "github.com/example/target"


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


def test_unreadable_record_is_activity_candidate(tmp_path: pathlib.Path) -> None:
    """破損記録を無活動へ変換せず、除外した会話だけを外す。"""
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
    """hookがない委譲先も登録簿の親子と終了を使って判定する。"""
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
    """SessionEndは祖先探索を行わずに会話の記録を削除する。"""
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


@pytest.fixture(name="daemons")
def _daemons() -> Iterator[list[session_activity.HostProcess]]:
    """会話を開かずに生存する2つの実プロセスを、ホストと同じ実行系名で渡す。"""
    with contextlib.ExitStack() as stack:
        processes = [
            stack.enter_context(process_tree.process_scope([sys.executable, "-c", "import time; time.sleep(120)"]))
            for _ in range(2)
        ]
        hosts = []
        for process in processes:
            identity = session_registry.ProcessIdentity.of(process.pid)
            assert identity is not None
            hosts.append(session_activity.HostProcess("codex", identity))
        yield hosts


@pytest.mark.parametrize("stage", ["acquire-second", "body", "release-one"])
def test_daemons_reclaims_every_acquired_process_on_failure(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    """2件目の獲得・利用・前段の解放で失敗しても、獲得済み全件の終了を確認する。"""
    acquired: list[subprocess.Popen[Any]] = []
    real_scope = process_tree.process_scope
    real_stop = process_tree.stop_process_tree

    @contextlib.contextmanager
    def scope(*args: Any, **kwargs: Any) -> Generator[subprocess.Popen[Any]]:
        if stage == "acquire-second" and acquired:
            raise RuntimeError("2件目の生成失敗")
        with real_scope(*args, **kwargs) as process:
            acquired.append(process)
            yield process

    def stop(process: subprocess.Popen[Any]) -> list[str]:
        result = real_stop(process)
        if stage == "release-one" and process is acquired[-1]:
            raise RuntimeError("前段の解放失敗")
        return result

    monkeypatch.setattr(process_tree, "process_scope", scope)
    monkeypatch.setattr(process_tree, "stop_process_tree", stop)
    lifetime = inspect.unwrap(_daemons)()
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


def _begin(*arguments: str) -> None:
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "submission", "begin", *arguments])
    assert exc.value.code == 0


def _end(session_id: str) -> None:
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "submission", "end", "--session-id", session_id])
    assert exc.value.code == 0


def _emit(session_id: str, event: str, *, host: session_activity.HostProcess | None = None, **extra: Any) -> None:
    session_activity.record_activity(
        {"session_id": session_id, "hook_event_name": event, **extra},
        process=host or _host("claude"),
    )


def _submitting(*targets: str) -> frozenset[str]:
    return session_activity.submission_activity(set(targets), processes=()).active_session_ids


def test_unrelated_work_does_not_create_start_activity(
    monkeypatch: pytest.MonkeyPatch,
    daemons: list[session_activity.HostProcess],
) -> None:
    """管理daemon・更新ループ、別リポジトリへの投入、同じリポジトリの別作業、属性を読めない記録では理由を返さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    for session_id in ("other-repo", "process-wi", "session-review", "collaboration", "broken-attribute"):
        _emit(session_id, "UserPromptSubmit")
    _begin("--session-id", "other-repo", "--target-repo", "https://github.com/example/other.git")
    directory = session_activity.submission_directory()
    (directory / "broken-attribute.json").write_text(
        json.dumps({"version": 1, "session_id": "broken-attribute", "kind": "add-awi-by-user"}),
        encoding="utf-8",
    )
    (directory / "process-wi.json").write_text(
        json.dumps({"version": 1, "session_id": "process-wi", "kind": "process-wi", "targets": [_TARGET]}),
        encoding="utf-8",
    )
    (session_activity.activity_directory() / "unreadable.json").write_text("{", encoding="utf-8")

    snapshot = session_activity.submission_activity({_TARGET}, processes=daemons)
    assert not snapshot.has_activity
    assert not snapshot.reasons
    # 作業種別を問わない取得では同じ会話を活動中と判定し、上の結果が稼働の判定に依らないことを確かめる。
    overall = session_activity.scan_activity(processes=daemons)
    assert overall.active_session_ids >= {"other-repo", "process-wi", "session-review", "collaboration"}
    assert not any("PID" in reason for reason in overall.reasons)
    assert session_activity.submission_activity({"github.com/example/other"}, processes=daemons).active_session_ids == {
        "other-repo"
    }


def test_submission_target_follows_actual_destination(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """手動の親会話とWI投入担当の双方で、起動cwdでなく実際の投入先とoriginで判定する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    target = init_repository(tmp_path / "target", origin="git@github.com:Example/Target.git", commit_message="init")
    worktree = tmp_path / "target-worktree"
    run_git(target, "worktree", "add", "--quiet", "-b", "other-worktree", str(worktree))
    elsewhere = init_repository(tmp_path / "elsewhere", origin="https://github.com/example/elsewhere.git")
    monkeypatch.chdir(elsewhere)

    # 手動の親会話だけ: cwdは別リポジトリで、投入先は同じoriginの別worktreeとして指定する。
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "manual")
    _emit("manual", "UserPromptSubmit")
    _begin("--target-repo", str(worktree))
    assert _submitting(_TARGET) == {"manual"}
    assert not _submitting("github.com/example/elsewhere")

    # WI投入担当を伴う場合: 親が応答待ちでも、親の投入先に属する担当の稼働を理由にする。
    transcript = _transcript(tmp_path, "claude", "delegating")
    _emit("delegating", "UserPromptSubmit", transcript_path=str(transcript))
    _begin("--session-id", "delegating", "--target-repo", "https://github.com/example/target")
    _emit("delegating", "Stop", transcript_path=str(transcript))
    session_registry.publish("add-wi-worker", terminal=False, launcher_session_id="delegating")
    session_registry.publish("unrelated-worker", terminal=False, launcher_session_id="someone-else")
    _end("manual")
    assert _submitting(_TARGET) == {"add-wi-worker"}
    assert not _submitting("github.com/example/elsewhere")
    session_registry.publish("add-wi-worker", terminal=True)
    assert not _submitting(_TARGET)


def test_submission_lifecycle_does_not_carry_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """終了・中止・対象変更・再開に追随し、後続の別作業と属性のない旧記録で待機を生じない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    _emit("legacy", "UserPromptSubmit")
    assert not _submitting(_TARGET)

    _emit("submitter", "UserPromptSubmit")
    _begin("--session-id", "submitter", "--target-repo", _TARGET)
    assert _submitting(_TARGET) == {"submitter"}
    # 対象変更
    _begin("--session-id", "submitter", "--target-repo", "github.com/example/changed")
    assert not _submitting(_TARGET)
    assert _submitting("github.com/example/changed") == {"submitter"}
    # 中止
    _emit("submitter", "Interrupt")
    _end("submitter")
    _emit("submitter", "UserPromptSubmit")
    assert not _submitting(_TARGET, "github.com/example/changed")
    # 再開: 会話の終了後に別プロセスで再開した会話は、残る属性で再び理由になる。
    _begin("--session-id", "submitter", "--target-repo", _TARGET)
    _emit("submitter", "SessionEnd")
    assert not _submitting(_TARGET)
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) as resumed:
        try:
            identity = session_registry.ProcessIdentity.of(resumed.pid)
            assert identity is not None
            _emit("submitter", "SessionStart", host=session_activity.HostProcess("claude", identity))
            assert _submitting(_TARGET) == {"submitter"}
            # 終了後の後続の別作業
            _end("submitter")
            _emit("submitter", "UserPromptSubmit", host=session_activity.HostProcess("claude", identity))
            assert not _submitting(_TARGET)
        finally:
            resumed.kill()
    assert not list(session_activity.submission_directory(state_paths.state_dir()).glob("*.json"))


def test_review_activity_limits_to_target_and_descendants(tmp_path, monkeypatch) -> None:
    """無関係な会話の活動を除き、対象の実ターン終了と子の活動を既存判定で扱う。"""
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
