# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの保存runの回復と再配送を検証する。"""

import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, notice_inbox, retained_results, shared_layout
from agent_toolkit._testing.agents_wait_support import _publish_result_on_sleep, _running_body, _write_own_status
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.agents_wait_support_contention import (
    _alive_followers,
    _interrupt_after_published,
    _leave_result_original,
    _LockedStepFailed,
    _run_contended_wait,
    _stash_notice,
    _stash_result,
    _write_current_wait_run,
)


@pytest.mark.parametrize(
    ("prepare", "expected"),
    [
        (_leave_result_original, {"session_id": "session-1", "status": "completed"}),
        (_stash_result, {"session_id": "session-1", "status": "completed"}),
        (
            _stash_notice,
            {
                "session_id": "session-1",
                "status": "running",
                "notices": [{"body": "通知"}],
            },
        ),
    ],
    ids=["original-result", "stashed-result", "stashed-notice"],
)
def test_wait_recovers_contended_running_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[pathlib.Path, pathlib.Path], None],
    expected: dict[str, object],
) -> None:
    """lock待ち中の未公開runから原本または退避済みの本文を配送する。"""
    assert _run_contended_wait(tmp_path, monkeypatch, prepare) == 0

    captured = capsys.readouterr()
    assert [_running_body(json.loads(line)) for line in captured.out.splitlines()] == [expected]
    assert not captured.err


def test_contended_wait_joins_follower_when_prepare_fails(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """準備が失敗しても、lockを解放して後発待機の終了を待ってから例外を伝える。"""

    def leave_result_then_fail(state_root: pathlib.Path, run_path: pathlib.Path) -> None:
        _leave_result_original(state_root, run_path)
        raise _LockedStepFailed

    with pytest.raises(_LockedStepFailed):
        _run_contended_wait(tmp_path, monkeypatch, leave_result_then_fail)

    assert not _alive_followers()
    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_wait_continues_after_contended_owner_aborts(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lock取得後も待機を続け、後から届く終了時の結果を受け取る。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 10.0)
    monkeypatch.setattr(agents_wait.time, "sleep", _publish_result_on_sleep(results))

    assert _run_contended_wait(tmp_path, monkeypatch) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_wait_recovers_target_added_during_lock_contention(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """先行待機がlock保持中に加えた対象と退避結果を引き継ぐ。"""

    def add_target(state_root: pathlib.Path, run_path: pathlib.Path) -> None:
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["targets"] = ["session-1", "session-2"]
        agents_wait._write_json(run_path, run)  # pylint: disable=protected-access
        results = shared_layout.results_directory("root-session", state_root)
        results.mkdir(parents=True, exist_ok=True)
        (results / "session-2.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        claimed, error = retained_results.take_result(
            "root-session",
            "session-2",
            "root.json",
            collector="test",
            state_root=state_root,
            stash_path=run_path.with_suffix("") / "results" / "session-2.json",
        )
        assert claimed == {"status": "completed"}
        assert error is None

    assert _run_contended_wait(tmp_path, monkeypatch, add_target) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-2", "status": "completed"}


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_stream"),
    [
        ("published", 0, "stdout"),
        ("running", 3, "stdout"),
    ],
)
def test_later_wait_uses_matching_current_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
    expected_code: int,
    expected_stream: str,
) -> None:
    """公開済みrunは回収し、残存する未公開runは待機を再開する。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, run_path = _write_current_wait_run(tmp_path, target="session-1", status=status)

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == expected_code
    )
    captured = capsys.readouterr()
    output = captured.out if expected_stream == "stdout" else captured.err
    if status == "published":
        assert json.loads(output) == {"session_id": "session-1", "status": "completed"}
        assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "consumed"
    else:
        assert _running_body(json.loads(output)) == {"session_id": "session-1", "status": "running"}
    assert json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8")) == {"run_id": "run-1"}


def test_wait_recovers_abandoned_run(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """先行プロセスがいない未公開runから終了時の結果を回収する。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "foreground-delivered"


@pytest.mark.parametrize("record", ["current", "run"])
@pytest.mark.parametrize("failure", ["json", "unicode", "permission", "shape"])
def test_wait_preserves_unreadable_recovery_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record: str,
    failure: str,
) -> None:
    """回復用記録の異常では既存runを保持し、読取回復後に同じ保存結果を受け取る。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    directory, run_path = _write_current_wait_run(tmp_path, target="session-1", status="published")
    target = directory / "current.json" if record == "current" else run_path
    original = target.read_bytes()

    def fail_open(path: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if path == target:
            raise PermissionError("読取拒否")
        return open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "permission":
            patch.setattr(pathlib.Path, "open", fail_open)
        else:
            target.write_bytes({"json": b"{", "unicode": b"\xff", "shape": b"[]"}[failure])
        assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 6
        captured = capsys.readouterr()
        assert not captured.out
        assert str(target) in captured.err
        assert set(directory.glob("*.json")) == {directory / "current.json", run_path}
    target.write_bytes(original)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


@pytest.mark.parametrize("original_exists", [False, True])
def test_wait_recovers_claimed_result_once(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    original_exists: bool,
) -> None:
    """退避済み結果を原本の有無によらず1回だけ公開する。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    status_path = _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    results.mkdir(parents=True)
    result_path = results / "session-1.json"
    result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    stash_path = run_path.with_suffix("") / "results" / "session-1.json"
    claimed, error = retained_results.take_result(
        "root-session", "session-1", "root.json", collector="test", state_root=tmp_path, stash_path=stash_path
    )
    assert claimed == {"status": "completed"}
    assert error is None
    assert stash_path.exists()
    if original_exists:
        result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    else:
        status_path.unlink()

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"session_id": "session-1", "status": "completed"}
    ]
    assert not result_path.exists()


@pytest.mark.parametrize("original_exists", [False, True])
def test_wait_recovers_claimed_notice_once(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    original_exists: bool,
) -> None:
    """退避済み通知を原本の有無によらず1回だけ公開する。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    notice_path = notices / "session-1.1.json"
    payload = {"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}
    notice_path.write_text(json.dumps(payload), encoding="utf-8")
    stash_directory = run_path.with_suffix("") / "notices"
    assert notice_inbox.take_notices(
        "root-session",
        "session-1",
        tmp_path,
        recipient_session_id="root-session",
        owner_status_file="root.json",
        stash_directory=stash_directory,
    ) == [{"body": payload["body"]}]
    if original_exists:
        notice_path.write_text(json.dumps(payload), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert [_running_body(json.loads(line)) for line in capsys.readouterr().out.splitlines()] == [
        {
            "session_id": "session-1",
            "status": "running",
            "notices": [{"body": payload["body"]}],
        }
    ]
    assert not notice_path.exists()


def test_wait_replays_published_run_after_publication_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run記録の確定後、標準出力への配送前に中断しても本文を再取得する。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    _interrupt_after_published(monkeypatch, tmp_path, capsys)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_wait_replays_published_notice_after_publication_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通知だけを持つ公開済みrunも前景配送前の中断から再演する。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    _interrupt_after_published(monkeypatch, tmp_path, capsys)

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert _running_body(json.loads(capsys.readouterr().out)) == {
        "session_id": "session-1",
        "status": "running",
        "notices": [{"body": "通知"}],
    }


def test_wait_replays_flushed_result_after_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flush後の中断では結果と通知を次の発行で1回再出力する。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    original_write = agents_wait._write_json  # pylint: disable=protected-access

    def interrupt_after_flush(path: pathlib.Path, value: dict[str, object]) -> None:
        if value.get("status") == "foreground-delivered":
            raise KeyboardInterrupt
        original_write(path, value)

    monkeypatch.setattr(agents_wait, "_write_json", interrupt_after_flush)
    with pytest.raises(KeyboardInterrupt):
        agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path)
    first = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert first == [
        {
            "session_id": "session-1",
            "status": "completed",
            "notices": [{"body": "通知"}],
        }
    ]

    monkeypatch.setattr(agents_wait, "_write_json", original_write)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == first
    run_directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
    current = json.loads((run_directory / "current.json").read_text(encoding="utf-8"))
    run_path = run_directory / f"{current['run_id']}.json"
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "consumed"
    assert not run_path.with_suffix("").exists()


@pytest.mark.parametrize("status", ["consumed", "foreground-delivered"])
@pytest.mark.parametrize("continuable", [False, True])
def test_later_wait_starts_new_run_for_consumed_current_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
    continuable: bool,
) -> None:
    """回収済みまたは前景配送済みの現行runと対象が一致しても新規runを開始する。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, _ = _write_current_wait_run(
        tmp_path,
        target="session-1",
        status=status,
        continuable=continuable,
    )

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 3
    )
    assert _running_body(json.loads(capsys.readouterr().out)) == {"session_id": "session-1", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


@pytest.mark.parametrize("status", ["consumed", "running", "published"])
def test_later_wait_starts_new_run_for_changed_targets(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    """対象が変わった後続waitは新規runを開始する。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-2"}])
    run_directory, _ = _write_current_wait_run(tmp_path, target="session-1", status=status)

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 3
    )
    assert _running_body(json.loads(capsys.readouterr().out)) == {"session_id": "session-2", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


def test_later_wait_starts_new_run_for_continuable_published_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """継続可能な公開runの旧出力を再演せず、新しい待機を開始する。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, _ = _write_current_wait_run(tmp_path, target="session-1", status="published", continuable=True)

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 3

    assert _running_body(json.loads(capsys.readouterr().out)) == {"session_id": "session-1", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"
