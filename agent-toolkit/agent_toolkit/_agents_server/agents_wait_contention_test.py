# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの競合と後続待機の回収を検証する。"""

import contextlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import types
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, shared_layout
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing import agents_wait_support_contention, isolation
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.agents_wait_support import _write_own_status
from agent_toolkit._testing.agents_wait_support_contention import (
    _CLI_WAIT_TIMEOUT_SECONDS,
    _alive_followers,
    _followers_under_lock,
    _join_threads,
    _LockedStepFailed,
    _observe_waiters,
    _wait_lock_path,
    _wait_while_owner_is_locked,
    _write_current_wait_run,
)


def test_join_threads_waits_all_until_deadline() -> None:
    """先にjoinするスレッドの終了後に後続を解放し、全ての終了を待つ。"""
    early_release = threading.Event()
    late_release = threading.Event()
    early_started = threading.Event()
    late_started = threading.Event()

    def finish(release: threading.Event, started: threading.Event) -> None:
        started.set()
        assert release.wait(timeout=5)

    class EarlyThread(threading.Thread):
        def join(self, timeout=None):
            early_release.set()
            super().join(timeout=timeout)
            late_release.set()

    early = EarlyThread(target=finish, args=(early_release, early_started), daemon=True)
    late = threading.Thread(target=finish, args=(late_release, late_started), daemon=True)
    unstarted = threading.Thread(target=late_release.wait, daemon=True)
    early.start()
    late.start()
    try:
        assert early_started.wait(5) and late_started.wait(5)
        assert _join_threads([early, late, unstarted], timeout=3.0) == []
        assert not late.is_alive()
    finally:
        early_release.set()
        late_release.set()
        early.join(timeout=5)
        late.join(timeout=5)
        assert not early.is_alive() and not late.is_alive()


def test_join_threads_waits_rest_when_first_stays_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """先頭の期限後も後続のjoinへ進み、生存する先頭だけを返す。"""
    released = threading.Event()
    finish_release = threading.Event()
    ready = threading.Event()
    clock = [100.0]
    monkeypatch.setattr(agents_wait_support_contention, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))

    class StuckThread(threading.Thread):
        def join(self, timeout=None):
            if not released.is_set():
                clock[0] += 1
                return super().join(timeout=0)
            return super().join(timeout=timeout)

    class FinishingThread(threading.Thread):
        def join(self, timeout=None):
            assert timeout == 0 or finish_release.is_set()
            finish_release.set()
            super().join(timeout=5)

    def finish() -> None:
        ready.set()
        assert finish_release.wait(5)

    stuck = StuckThread(target=released.wait, args=(5,), name="stuck", daemon=True)
    finishing = FinishingThread(target=finish, name="finishing", daemon=True)
    stuck.start()
    finishing.start()
    try:
        assert ready.wait(5)
        assert _join_threads([stuck, finishing], timeout=0.05) == ["stuck"]
        assert not finishing.is_alive()
    finally:
        released.set()
        finish_release.set()
        stuck.join(timeout=5)
        finishing.join(timeout=5)
        assert not stuck.is_alive() and not finishing.is_alive()


def test_agents_wait_rejects_overlapping_owner(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """旧形式のlock競合は識別可能な診断を添えて終了コード8で拒否する。"""
    assert _wait_while_owner_is_locked(tmp_path, "session-1") == 8

    assert _wait_lock_path(tmp_path).exists()
    error = capsys.readouterr().err
    assert "旧形式の待機がlockを保持" in error
    assert "targets=session-1" in error
    assert f"\n{NEXT_ACTION_PREFIX}" in error
    assert "atk agents wait" in error


@pytest.mark.parametrize("failure", [False, True], ids=["normal", "verification-fails"])
@pytest.mark.parametrize("notification", [False, True])
def test_followers_join_the_same_wait_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    notification: bool,
    failure: bool,
) -> None:
    """先行待機の配信後、2件の後発待機は保存runの参照だけを返す。

    配信後の検証が失敗した場合も、例外を伝える前に両方の後発待機を回収する。
    """
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: _CLI_WAIT_TIMEOUT_SECONDS)
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    run_directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
    run_path = run_directory / "run-1.json"
    agents_wait._write_json(  # pylint: disable=protected-access
        run_path,
        {
            "run_id": "run-1",
            "status": "running",
            "targets": ["session-1"],
            "started_at": "2026-09-21T00:00:00+00:00",
        },
    )
    agents_wait._write_json(run_directory / "current.json", {"run_id": "run-1"})  # pylint: disable=protected-access

    original_acquire, followers_waiting = _observe_waiters(monkeypatch, 2)
    results: list[int] = []

    def follow() -> None:
        results.append(
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
        )

    delivered: dict[str, Any] = {"session_id": "session-1", "status": "running" if notification else "completed"}
    if notification:
        delivered["notices"] = [{"body": "通知"}]

    def publish() -> None:
        agents_wait._publish_wait_result(  # pylint: disable=protected-access
            run_path,
            json.dumps(delivered, ensure_ascii=False, separators=(",", ":")),
            0,
        )
        if failure:
            raise _LockedStepFailed

    with pytest.raises(_LockedStepFailed) if failure else contextlib.nullcontext():
        _followers_under_lock(lock_path, original_acquire, followers_waiting, follow, 2, publish)

    assert not _alive_followers()
    assert sorted(results) == [8, 8]
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output.count(delivered) == 1
    consumed = [item for item in output if item.get("status") == "consumed"]
    assert len(consumed) == 2
    for response in consumed:
        assert response["recovery"]["run_path"] == str(run_path.resolve())
    assert json.loads(json.loads(run_path.read_text(encoding="utf-8"))["output"]) == delivered


def test_agents_wait_contention_diagnostic_recovers_collected_body(
    wait_environment: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """期限時のrun記録から回収済み本文のパスへ戻り、本文ファイルを読める。run記録は本文を重ねて持たない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    wait_environment.mkdir(parents=True, exist_ok=True)
    original = wait_environment / "session-1.json"
    original.write_text(json.dumps({"status": "completed", "agent_message": "## 回収済み本文\n結果"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    delivered = json.loads(capsys.readouterr().out)
    assert not original.exists()
    directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
    run_id = json.loads((directory / "current.json").read_text(encoding="utf-8"))["run_id"]
    run_path = directory / f"{run_id}.json"
    saved = run_path.read_bytes()
    probe = tmp_path / "wait_probe.py"
    probe.write_text(
        "import pathlib, sys\n"
        "from agent_toolkit import atk\n"
        "from agent_toolkit._agents_server import agents_wait\n"
        "from agent_toolkit._common import state_paths\n"
        "state_root = pathlib.Path(sys.argv[1])\n"
        "state_paths.state_dir = lambda: state_root\n"
        "clock = [0.0]\n"
        "agents_wait.time.monotonic = lambda: clock[0]\n"
        "def advance(seconds):\n"
        "    clock[0] += 3600.0\n"
        "agents_wait.time.sleep = advance\n"
        "atk.main(['agents', 'wait'])\n",
        encoding="utf-8",
    )
    environment = host_environ()
    # ランタイムのPATHを引き継ぎ、CLIの掃引・設定はテスト専用領域へ閉じる。
    for name in (*isolation.HOME_ENVIRONMENT_NAMES, *isolation.CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
        if name in os.environ:
            environment[name] = os.environ[name]
        else:
            environment.pop(name, None)
    environment["FORCE_PROMPT_CACHING_5M"] = "1"
    lock_path = _wait_lock_path(tmp_path)
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=False)
        try:
            completed = subprocess.run(
                [sys.executable, str(probe), str(tmp_path)],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )
        finally:
            release_lock(lock_file)
    assert completed.returncode == 8, completed.stderr
    assert not completed.stdout
    diagnostic = completed.stderr
    assert f"run_path={run_path}" in diagnostic
    assert "agent_message_path" in diagnostic
    assert run_path.read_bytes() == saved

    record = json.loads(run_path.read_text(encoding="utf-8"))
    terminal = json.loads(record["output"])
    assert "agent_message" not in terminal
    assert terminal == {key: value for key, value in delivered.items() if key != "agent_message"}
    assert pathlib.Path(terminal["agent_message_path"]).read_text(encoding="utf-8") == delivered["agent_message"]


@pytest.mark.parametrize("failure", [False, True], ids=["normal", "verification-fails"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_wait_replays_follower_output_after_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stream: str,
    failure: bool,
) -> None:
    """未配送runの回復がflush後に停止した場合は、別waitが本文を回収する。

    lock競合への到達後の検証が失敗した場合も、例外を伝える前に両方の後発待機を回収する。
    """
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: _CLI_WAIT_TIMEOUT_SECONDS)
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="published")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["stream"] = stream
    agents_wait._write_json(run_path, run)  # pylint: disable=protected-access
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)

    original_write = agents_wait._write_json  # pylint: disable=protected-access
    interrupted = False

    def interrupt_first_consume(path: pathlib.Path, value: dict[str, object]) -> None:
        nonlocal interrupted
        if value.get("status") == "consumed" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        original_write(path, value)

    monkeypatch.setattr(agents_wait, "_write_json", interrupt_first_consume)
    original_acquire, followers_waiting = _observe_waiters(monkeypatch, 2)
    outcomes: list[int | str] = []

    def follow() -> None:
        try:
            outcomes.append(
                agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path)
            )
        except KeyboardInterrupt:
            outcomes.append("interrupted")

    def fail_verification() -> None:
        raise _LockedStepFailed

    with pytest.raises(_LockedStepFailed) if failure else contextlib.nullcontext():
        _followers_under_lock(lock_path, original_acquire, followers_waiting, follow, 2, fail_verification if failure else None)

    assert not _alive_followers()
    assert outcomes.count("interrupted") == 1
    assert outcomes.count(0) == 1
    captured = capsys.readouterr()
    delivered = captured.err if stream == "stderr" else captured.out
    assert [json.loads(line) for line in delivered.splitlines()] == [
        {"session_id": "session-1", "status": "completed"},
        {"session_id": "session-1", "status": "completed"},
    ]
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "consumed"
