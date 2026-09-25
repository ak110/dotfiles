"""atk agents waitの公開CLI契約を検証する。"""

import json
import pathlib
import threading
from collections.abc import Callable

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, logging_config, session_registry, state, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.file_lock import acquire_lock, release_lock


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """結果の無い待機を即時に返して公開引数へ上限を露出させない。"""
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(logging_config, "user_state_dir", lambda *_args, **_kwargs: str(tmp_path / "logs"))


@pytest.fixture(name="wait_environment")
def _wait_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """状態ファイルの解決先をテスト用ディレクトリへ隔離する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_STATUS_HOST_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_atk_config, "state_dir", lambda: tmp_path)
    return status_file.results_directory("root-session", tmp_path)


def _write_own_status(results_directory: pathlib.Path, sessions: list[object]) -> pathlib.Path:
    """自身の書込主体の状態ファイルを作成する。"""
    root_status = results_directory.parent / "root.json"
    root_status.parent.mkdir(parents=True, exist_ok=True)
    root_status.write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")
    return root_status


def _publish_result_on_sleep(results_directory: pathlib.Path) -> Callable[[float], None]:
    """sleepの代わりに`session-1`の終端結果を公開する関数を返す。"""

    def publish_result(_seconds: float) -> None:
        results_directory.mkdir(parents=True, exist_ok=True)
        (results_directory / "session-1.json").write_text(
            json.dumps({"status": "completed"}),
            encoding="utf-8",
        )

    return publish_result


def _wait_for_session_1_result(results_directory: pathlib.Path) -> int:
    """テスト用のroot sessionから`session-1`の結果を待つ。"""
    return agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=results_directory.parents[2],
    )


def test_agents_wait_outputs_every_retained_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未回収の終端結果を識別子順のJSON Linesで全件返し、回収した結果ファイルを残さない。"""
    wait_environment.mkdir(parents=True)
    first = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    second = {"session_id": "session-2", "status": "failed"}
    (wait_environment / "session-1.json").write_text(json.dumps(first), encoding="utf-8")
    (wait_environment / "session-2.json").write_text(json.dumps(second), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [first, second]
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()
    assert not (wait_environment / "session-2.json").exists()


def test_agents_wait_uses_explicit_root_without_environment(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """起動応答のルートを明示すれば環境の別名なしで終端結果を回収できる。"""
    results = status_file.results_directory("mcp-root", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True, exist_ok=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={}, state_root=tmp_path, root_session_id="mcp-root") == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_agents_wait_delivers_collected_results_before_reporting_read_failure(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解釈できない結果ファイルがある巡回でも、回収済みの本文を保持したまま配送する。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed"}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    unreadable = wait_environment / "session-2.json"
    unreadable.write_text("{不正なJSON", encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [payload]
    assert not captured.err
    assert not (wait_environment / "session-1.json").exists()
    assert unreadable.exists()

    assert _wait_for_session_1_result(wait_environment) == 6
    assert "終端結果ファイルを読めません" in capsys.readouterr().err


def test_agents_wait_outputs_terminal_result_with_notice_only_session(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端結果を持つsessionと通知だけを持つsessionが同時にある場合は双方の行を返す。"""
    wait_environment.mkdir(parents=True)
    terminal = {"session_id": "session-1", "status": "completed"}
    (wait_environment / "session-1.json").write_text(json.dumps(terminal), encoding="utf-8")
    _write_own_status(wait_environment, [{"session_id": "session-2"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    sent_at = "2026-09-15T00:00:01+00:00"
    (notices / "session-2.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-2", "sent_at": sent_at, "body": "検査コマンドが未導入"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [
        terminal,
        {
            "session_id": "session-2",
            "status": "running",
            "notices": [{"sent_at": sent_at, "body": "検査コマンドが未導入"}],
        },
    ]
    assert not (wait_environment / "session-1.json").exists()
    assert not any(notices.iterdir())


def test_agents_wait_does_not_consume_another_writer_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じroot配下でも別の状態書込主体が公開した結果は回収しない。"""
    root = status_file.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "delegate-session.json").write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "owned-session"}]}),
        encoding="utf-8",
    )
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir()
    foreign_result = results / "foreign-session.json"
    foreign_result.write_text(
        json.dumps({"status": "completed", "owner_status_file": "root.json"}),
        encoding="utf-8",
    )

    assert (
        agents_wait.wait_for_result(
            environment={
                "AGENT_TOOLKIT_OWNER_SESSION": "root-session",
                "AGENT_TOOLKIT_STATUS_HOST_SESSION": "delegate-session",
            },
            state_root=tmp_path,
        )
        == 3
    )

    assert json.loads(capsys.readouterr().out) == {"session_id": "owned-session", "status": "running"}
    assert foreign_result.exists()


def test_root_wait_does_not_consume_delegate_writer_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ルート待機は所有者を持つ委譲先の結果を旧形式として回収しない。"""
    root = status_file.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "root.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir()
    delegate_result = results / "delegate-result.json"
    delegate_result.write_text(
        json.dumps({"status": "completed", "owner_status_file": "delegate-session.json"}),
        encoding="utf-8",
    )

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 10
    )

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert delegate_result.exists()


def test_delegate_wait_consumes_own_writer_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先待機は結果ファイルの所有者が自身と一致する結果を回収する。"""
    root = status_file.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "delegate-session.json").write_text(
        json.dumps({"version": 1, "sessions": []}),
        encoding="utf-8",
    )
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir()
    own_result = results / "delegate-result.json"
    own_result.write_text(
        json.dumps(
            {
                "status": "completed",
                "agent_message": "委譲先の結果",
                "owner_status_file": "delegate-session.json",
            }
        ),
        encoding="utf-8",
    )

    assert (
        agents_wait.wait_for_result(
            environment={
                "AGENT_TOOLKIT_OWNER_SESSION": "root-session",
                "AGENT_TOOLKIT_STATUS_HOST_SESSION": "delegate-session",
            },
            state_root=tmp_path,
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "session_id": "delegate-result",
        "status": "completed",
        "agent_message": "委譲先の結果",
    }
    assert not own_result.exists()


def test_codex_delegate_wait_resolves_writer_alias_and_releases_target(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codex threadと異なる書込主体の稼働状態と終端結果を同じ所有単位で回収する。"""
    root = status_file.status_directory("root-session", tmp_path)
    root.mkdir(parents=True)
    (root / "writer-session.json").write_text(
        json.dumps({"version": 1, "host_session_id": "codex-thread", "sessions": [{"session_id": "remote-session"}]}),
        encoding="utf-8",
    )
    status_file.write_host_alias("root-session", "writer-session", "codex-thread", tmp_path)
    environment = {"AGENT_TOOLKIT_OWNER_SESSION": "root-session", "CODEX_THREAD_ID": "codex-thread"}

    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 3
    assert json.loads(capsys.readouterr().out) == {"session_id": "remote-session", "status": "running"}

    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(exist_ok=True)
    result_path = results / "remote-session.json"
    result_path.write_text(
        json.dumps({"status": "completed", "owner_status_file": "writer-session.json"}),
        encoding="utf-8",
    )
    (status_file.hosts_directory("root-session", tmp_path) / "writer-session.json").unlink()

    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"session_id": "remote-session", "status": "completed"}
    assert not result_path.exists()
    retained, error = status_file.read_wait_targets("root-session", "writer-session.json", tmp_path)
    assert retained == set()
    assert error is None


def test_codex_delegate_wait_reports_ambiguous_writer_aliases(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数の書込主体へ対応するCodex threadは診断を返して待機しない。"""
    status_file.write_host_alias("root-session", "writer-a", "codex-thread", tmp_path)
    status_file.write_host_alias("root-session", "writer-b", "codex-thread", tmp_path)

    assert (
        agents_wait.wait_for_result(
            environment={"AGENT_TOOLKIT_OWNER_SESSION": "root-session", "CODEX_THREAD_ID": "codex-thread"},
            state_root=tmp_path,
        )
        == 4
    )

    assert "書込主体を一意に解決できません" in capsys.readouterr().err


def _wait_lock_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """root書込主体の待機所有権を表すロックの経路を返す。"""
    return status_file.status_directory("root-session", tmp_path) / "wait-locks" / "root.json.lock"


def _write_current_wait_run(
    tmp_path: pathlib.Path,
    *,
    target: str,
    status: str,
    continuable: bool = False,
) -> tuple[pathlib.Path, pathlib.Path]:
    """指定したrunをcurrentとして保存し、runディレクトリと記録経路を返す。"""
    run_directory = status_file.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
    run_path = run_directory / "run-1.json"
    agents_wait._write_json(  # pylint: disable=protected-access
        run_path,
        {
            "run_id": "run-1",
            "status": status,
            "targets": [target],
            "started_at": "2026-09-21T00:00:00+00:00",
            "continuable": continuable,
            "output": f'{{"session_id":"{target}","status":"completed"}}',
            "exit_code": 0,
            "stream": "stdout",
        },
    )
    agents_wait._write_json(run_directory / "current.json", {"run_id": "run-1"})  # pylint: disable=protected-access
    return run_directory, run_path


def _observe_waiters(monkeypatch: pytest.MonkeyPatch, expected_count: int) -> tuple[Callable[..., None], threading.Event]:
    """指定件数の後続waitがlock競合へ到達した時点を通知する。"""
    original_acquire = agents_wait.acquire_lock
    followers_waiting = threading.Event()
    count_lock = threading.Lock()
    failed_nonblocking = 0

    def observe_acquire(lock_file, *, blocking: bool):
        nonlocal failed_nonblocking
        try:
            return original_acquire(lock_file, blocking=blocking)
        except OSError:
            if not blocking:
                with count_lock:
                    failed_nonblocking += 1
                    if failed_nonblocking == expected_count:
                        followers_waiting.set()
            raise

    monkeypatch.setattr(agents_wait, "acquire_lock", observe_acquire)
    return original_acquire, followers_waiting


def _run_contended_wait(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[pathlib.Path, pathlib.Path], None] | None = None,
) -> int:
    """後発待機がlockに並んだ後、未公開runを残して所有権を渡す。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    original_acquire, follower_waiting = _observe_waiters(monkeypatch, 1)
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    outcomes: list[int] = []

    def follow() -> None:
        outcomes.append(
            agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path)
        )

    with lock_path.open("a+b") as lock_file:
        original_acquire(lock_file, blocking=False)
        follower = threading.Thread(target=follow)
        try:
            follower.start()
            assert follower_waiting.wait(timeout=5)
            if prepare is not None:
                prepare(tmp_path, run_path)
        finally:
            release_lock(lock_file)
        follower.join(timeout=5)
        assert not follower.is_alive()
    assert len(outcomes) == 1
    return outcomes[0]


def _leave_result_original(tmp_path: pathlib.Path, _run_path: pathlib.Path) -> None:
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True, exist_ok=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")


def _stash_result(tmp_path: pathlib.Path, run_path: pathlib.Path) -> None:
    _leave_result_original(tmp_path, run_path)
    claimed, error = status_file.take_result(
        "root-session",
        "session-1",
        "root.json",
        collector="test",
        state_root=tmp_path,
        stash_path=run_path.with_suffix("") / "results" / "session-1.json",
    )
    assert claimed == {"status": "completed"}
    assert error is None


def _stash_notice(tmp_path: pathlib.Path, run_path: pathlib.Path) -> None:
    notices = status_file.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    assert status_file.take_notices(
        "root-session", "session-1", tmp_path, stash_directory=run_path.with_suffix("") / "notices"
    ) == [{"sent_at": "2026-09-25T00:00:00Z", "body": "通知"}]


def _interrupt_after_published(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """run記録の公開後、前景出力の前に待機を中断する。"""
    original_write = agents_wait._write_json  # pylint: disable=protected-access

    def interrupt_publication(path: pathlib.Path, value: dict[str, object]) -> None:
        original_write(path, value)
        if value.get("status") == "published":
            raise KeyboardInterrupt

    monkeypatch.setattr(agents_wait, "_write_json", interrupt_publication)
    with pytest.raises(KeyboardInterrupt):
        agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path)
    monkeypatch.setattr(agents_wait, "_write_json", original_write)
    assert not capsys.readouterr().out


def _wait_while_owner_is_locked(tmp_path: pathlib.Path, own_session_id: str) -> int:
    """別実行が同じ書込主体の待機所有権を保持する状態で待機を発行する。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": own_session_id}])
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=False)
        try:
            return agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
        finally:
            release_lock(lock_file)


def test_agents_wait_rejects_overlapping_owner(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """旧形式のlock競合は識別可能な診断を添えて終了コード8で拒否する。"""
    assert _wait_while_owner_is_locked(tmp_path, "session-1") == 8

    assert _wait_lock_path(tmp_path).exists()
    error = capsys.readouterr().err
    assert "旧形式の待機がlockを保持" in error
    assert "targets=session-1" in error


def test_followers_join_the_same_wait_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同時に発行した2件の後発待機は先行runへ固定し、結果を1回だけ再演する。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    run_directory = status_file.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
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

    with lock_path.open("a+b") as lock_file:
        original_acquire(lock_file, blocking=False)
        followers = [threading.Thread(target=follow) for _ in range(2)]
        for follower in followers:
            follower.start()
        assert followers_waiting.wait(timeout=5)
        agents_wait._publish_wait_result(  # pylint: disable=protected-access
            run_path,
            '{"session_id":"session-1","status":"completed"}',
            0,
        )
        release_lock(lock_file)
        for follower in followers:
            follower.join(timeout=5)
            assert not follower.is_alive()

    assert sorted(results) == [0, 8]
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {"session_id": "session-1", "status": "completed"} in output
    assert {"status": "consumed", "run_id": "run-1"} in output


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_wait_replays_follower_output_after_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stream: str,
) -> None:
    """後続waitがflush後に停止しても、lock待ちの別waitが本文を回収する。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="foreground-delivered")
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

    with lock_path.open("a+b") as lock_file:
        original_acquire(lock_file, blocking=False)
        try:
            followers = [threading.Thread(target=follow) for _ in range(2)]
            for follower in followers:
                follower.start()
            assert followers_waiting.wait(timeout=5)
        finally:
            release_lock(lock_file)
        for follower in followers:
            follower.join(timeout=5)
            assert not follower.is_alive()

    assert outcomes.count("interrupted") == 1
    assert outcomes.count(0) == 1
    captured = capsys.readouterr()
    delivered = captured.err if stream == "stderr" else captured.out
    assert [json.loads(line) for line in delivered.splitlines()] == [
        {"session_id": "session-1", "status": "completed"},
        {"session_id": "session-1", "status": "completed"},
    ]
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "consumed"


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
                "notices": [{"sent_at": "2026-09-25T00:00:00Z", "body": "通知"}],
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
    """lock待ち中の未公開runから原本又は退避済みの本文を配送する。"""
    assert _run_contended_wait(tmp_path, monkeypatch, prepare) == 0

    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == [expected]
    assert not captured.err


def test_wait_continues_after_contended_owner_aborts(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lock取得後も待機を続け、後から届く終端結果を受け取る。"""
    results = status_file.results_directory("root-session", tmp_path)
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 10.0)
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
        results = status_file.results_directory("root-session", state_root)
        results.mkdir(parents=True, exist_ok=True)
        (results / "session-2.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        claimed, error = status_file.take_result(
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
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
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
        assert json.loads(output) == {"session_id": "session-1", "status": "running"}
    assert json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8")) == {"run_id": "run-1"}


def test_wait_recovers_abandoned_run(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """先行プロセスがいない未公開runから終端結果を回収する。"""
    results = status_file.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "foreground-delivered"


@pytest.mark.parametrize("original_exists", [False, True])
def test_wait_recovers_claimed_result_once(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    original_exists: bool,
) -> None:
    """退避済み結果を原本の有無によらず1回だけ公開する。"""
    results = status_file.results_directory("root-session", tmp_path)
    status_path = _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    results.mkdir(parents=True)
    result_path = results / "session-1.json"
    result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    stash_path = run_path.with_suffix("") / "results" / "session-1.json"
    claimed, error = status_file.take_result(
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
    results = status_file.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    notices = status_file.notices_directory("root-session", tmp_path)
    notices.mkdir()
    notice_path = notices / "session-1.1.json"
    payload = {"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}
    notice_path.write_text(json.dumps(payload), encoding="utf-8")
    stash_directory = run_path.with_suffix("") / "notices"
    assert status_file.take_notices("root-session", "session-1", tmp_path, stash_directory=stash_directory) == [
        {"sent_at": payload["sent_at"], "body": payload["body"]}
    ]
    if original_exists:
        notice_path.write_text(json.dumps(payload), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {
            "session_id": "session-1",
            "status": "running",
            "notices": [{"sent_at": payload["sent_at"], "body": payload["body"]}],
        }
    ]
    assert not notice_path.exists()


def test_wait_replays_published_run_after_publication_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run記録の確定後、標準出力への配送前に中断しても本文を再取得する。"""
    results = status_file.results_directory("root-session", tmp_path)
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
    results = status_file.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    notices = status_file.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    _interrupt_after_published(monkeypatch, tmp_path, capsys)

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {
        "session_id": "session-1",
        "status": "running",
        "notices": [{"sent_at": "2026-09-25T00:00:00Z", "body": "通知"}],
    }


def test_wait_replays_flushed_result_after_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """flush後の中断では結果と通知を次の発行で1回再出力する。"""
    results = status_file.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    notices = status_file.notices_directory("root-session", tmp_path)
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
            "notices": [{"sent_at": "2026-09-25T00:00:00Z", "body": "通知"}],
        }
    ]

    monkeypatch.setattr(agents_wait, "_write_json", original_write)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == first
    run_directory = status_file.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
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
    """回収済み又は前景配送済みの現行runと対象が一致しても新規runを開始する。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
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
    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


@pytest.mark.parametrize("status", ["consumed", "running", "published"])
def test_later_wait_starts_new_run_for_changed_targets(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    """対象が変わった後続waitは先行runではなく新規runを開始する。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-2"}])
    run_directory, _ = _write_current_wait_run(tmp_path, target="session-1", status=status)

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out) == {"session_id": "session-2", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


def test_later_wait_starts_new_run_for_continuable_published_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """継続可能な公開runの旧出力を再演せず、新しい待機を開始する。"""
    _write_own_status(status_file.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, _ = _write_current_wait_run(tmp_path, target="session-1", status="published", continuable=True)

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 3

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


def test_agents_wait_rejects_disjoint_sets_for_same_owner(tmp_path: pathlib.Path) -> None:
    """対象集合が別でも同じ書込主体の重複待機を許可しない。"""
    assert _wait_while_owner_is_locked(tmp_path, "session-2") == 8


def test_agents_wait_resolves_changed_conversation_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話のsession識別子が変わった後も索引先の終端結果を回収する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "current-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_atk_config, "state_dir", lambda: tmp_path)
    results = status_file.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed"}
    (results / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    status_file.write_root_alias("current-session", "root-session", tmp_path)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    assert json.loads(capsys.readouterr().out) == payload
    assert not (results / "session-1.json").exists()


def test_agents_wait_reports_absent_targets_as_failure(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """root対応を確認できない空状態はMCP一覧による復旧を案内する。"""
    assert not wait_environment.exists()

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "CLIが解決したroot=root-session" in captured.err
    assert "MCPの`list`を1回" in captured.err
    assert "`atk agents wait`を再実行" in captured.err
    assert "待機対象の登録が0件" not in captured.err


def test_agents_wait_reports_confirmed_empty_root_as_absent_targets(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """確認済みrootで待機対象も保持sessionも無い場合は既存の終了区分を保つ。"""
    _write_own_status(wait_environment, [])

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert "保持中のsessionも0件" in captured.err


def test_agents_wait_keeps_waiting_once_a_target_is_registered(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機対象を1件でも取得した待機へは対象不在の上限を適用しない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_waiting_for_starting_session(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """startingのsessionが保持中なら対象不在として即時終了しない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1", "status": "starting"}])
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_captured_session_after_projection_disappears(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機開始後に状態投影が消えても取得済みsessionを非終端として返す。"""
    status_path = _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monotonic_values = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(agents_wait.time, "sleep", lambda _seconds: status_path.unlink())

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_keeps_waiting_for_retained_session(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状態ファイルが保持するsessionを消失として返さない。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"session_id": "session-1", "status": "running"}
    assert not captured.err


def test_agents_wait_reports_absence_without_own_status_file(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自身の書込主体がsessionを保持しない場合は別主体の投影を待機対象にしない。"""
    nested_status = wait_environment.parent / "child-session.json"
    nested_status.parent.mkdir(parents=True)
    nested_status.write_text(
        json.dumps({"version": 1, "sessions": [{"session_id": "session-1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "保持中のsessionも0件" in captured.err


@pytest.mark.parametrize("root_body", ["{", "[]"])
def test_agents_wait_ignores_unreadable_root_status(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    root_body: str,
) -> None:
    """状態ファイルを解釈できない場合は従来の待機を継続する。"""
    root_status = wait_environment.parent / "root.json"
    root_status.parent.mkdir(parents=True)
    root_status.write_text(root_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "running"}
    assert not captured.err


@pytest.mark.parametrize("result_body", ["[]", "{"])
def test_agents_wait_rejects_corrupted_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    result_body: str,
) -> None:
    """破損した終端結果は待機上限と別の終了コードで停止する。"""
    wait_environment.mkdir(parents=True)
    (wait_environment / "session-1.json").write_text(result_body, encoding="utf-8")

    with pytest.raises(SystemExit, match="6"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert str(wait_environment / "session-1.json") in captured.err


@pytest.mark.parametrize("session_id", ["../outside", ""])
def test_agents_wait_rejects_invalid_session_in_status_file(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    session_id: str,
) -> None:
    """形式外のsession識別子を結果ファイルの解決前に拒否する。"""
    _write_own_status(wait_environment, [{"session_id": session_id}])

    with pytest.raises(SystemExit, match="5"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "session_idの形式が不正" in captured.err


def test_agents_wait_rejects_turn(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去したturn指定はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "wait", "--" + "turn=1"])
    assert not capsys.readouterr().out


def test_agents_wait_rejects_positional_session(capsys: pytest.CaptureFixture[str]) -> None:
    """撤去した待機対象の位置引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "wait", "session-1"])
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    "environment",
    [{}, {"AGENT_TOOLKIT_OWNER_SESSION": "root-session"}],
    ids=["no-root-session", "owner-without-host-session"],
)
def test_missing_state_dir_reports_alternative(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
) -> None:
    """状態ファイルの書込主体を解決できない場合は終了コード4を返す。"""
    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 4

    captured = capsys.readouterr()
    assert not captured.out
    assert "状態ディレクトリを解決できません" in captured.err
    assert "`atk agents list`と`atk agents wait`" in captured.err


def test_agents_wait_returns_notices_while_running(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """終端前の通知を回収し、running状態とともに1行で返す。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    for sequence, sent_at, body in (
        (2, "2026-09-07T00:00:02+00:00", "後の通知"),
        (1, "2026-09-07T00:00:01+00:00", "先の通知"),
    ):
        payload = {"version": 1, "session_id": "session-1", "sent_at": sent_at, "body": body}
        (notices / f"session-1.{sequence}.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert response == {
        "session_id": "session-1",
        "status": "running",
        "notices": [
            {"sent_at": "2026-09-07T00:00:01+00:00", "body": "先の通知"},
            {"sent_at": "2026-09-07T00:00:02+00:00", "body": "後の通知"},
        ],
    }
    assert not any(notices.iterdir())


def test_agents_wait_reissues_for_registered_session_after_notice(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通知後に状態投影が消えても、再発行した待機が同じsessionの終端結果を回収する。"""
    session_registry.publish("session-1", terminal=False, state_root=wait_environment.parents[2])
    status_path = _write_own_status(wait_environment, [{"session_id": "session-1"}])
    notices = wait_environment.parent / "notices"
    notices.mkdir(parents=True)
    (notices / "session-1.1.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session_id": "session-1",
                "sent_at": "2026-09-14T00:00:00+00:00",
                "body": "通知",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])
    assert json.loads(capsys.readouterr().out)["status"] == "running"
    status_path.unlink()

    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 10.0)
    monkeypatch.setattr(agents_wait.time, "sleep", _publish_result_on_sleep(wait_environment))

    assert _wait_for_session_1_result(wait_environment) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-1"}


def test_agents_wait_releases_registered_session_missing_from_registry(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状態・結果・session登録簿から失われた待機対象を再発行時に解放する。"""
    status_file.retain_wait_targets("root-session", "root.json", ["session-1"], wait_environment.parents[2])

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    retained, error = status_file.read_wait_targets(
        "root-session",
        "root.json",
        wait_environment.parents[2],
    )
    assert retained == set()
    assert error is None


def test_agents_wait_releases_registered_session_terminal_in_registry(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """登録簿が終端を示し、結果も状態ファイルの行も無い待機対象を解放して待たずに終える。"""
    state_root = wait_environment.parents[2]
    status_file.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    session_registry.publish("session-1", terminal=True, status="interrupted", state_root=state_root)

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    retained, error = status_file.read_wait_targets("root-session", "root.json", state_root)
    assert retained == set()
    assert error is None


def test_agents_wait_ends_when_only_target_becomes_terminal_without_result(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機中に唯一の対象が結果を残さず終端した場合は、待機上限を待たずに対象不在で終える。"""
    state_root = wait_environment.parents[2]
    status_file.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    session_registry.publish("session-1", terminal=False, state_root=state_root)
    # 上限到達による終了と区別するため、待機上限を検体の実行時間より十分大きくする。
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 3600.0)
    sleeps: list[float] = []

    def terminate_on_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        session_registry.publish("session-1", terminal=True, status="interrupted", state_root=state_root)

    monkeypatch.setattr(agents_wait.time, "sleep", terminate_on_sleep)

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象の登録が0件" in captured.err
    assert len(sleeps) == 1
    retained, error = status_file.read_wait_targets("root-session", "root.json", state_root)
    assert retained == set()
    assert error is None


def test_agents_wait_collects_registered_terminal_result_and_releases_target(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostToolUseが保持した対象の終端結果を回収し、待機対象登録を解除する。"""
    state_root = wait_environment.parents[2]
    status_file.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    wait_environment.mkdir(parents=True)
    (wait_environment / "session-1.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-1"}
    retained, error = status_file.read_wait_targets("root-session", "root.json", state_root)
    assert retained == set()
    assert error is None


def test_agents_wait_releases_corrupted_wait_target(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """読取不能な待機対象登録を解放し、理由を伴う終了コード9で返す。"""
    directory = status_file.wait_targets_directory("root-session", "root.json", wait_environment.parents[2])
    directory.mkdir(parents=True)
    marker = directory / "session-1.json"
    marker.write_text("{", encoding="utf-8")

    with pytest.raises(SystemExit, match="9"):
        atk.main(["agents", "wait"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "待機対象登録簿を読めません" in captured.err
    assert not marker.exists()


def test_agents_wait_reports_seconds_since_output_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機上限の応答はsessionの活動時刻と最新テキスト出力時刻を別項目で返す。"""
    output_updated_at = "2026-09-09T00:00:00+00:00"
    updated_at = "2099-01-01T00:00:00+00:00"
    _write_own_status(
        wait_environment,
        [{"session_id": "session-1", "output_updated_at": output_updated_at, "updated_at": updated_at}],
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert response["output_updated_at"] == output_updated_at
    assert response["updated_at"] == updated_at


@pytest.mark.parametrize(
    ("sessions", "expected"),
    [
        ([None], {"status": "running"}),
        (
            [{"session_id": "session-1", "started_at": "2026-09-09T00:00:00"}],
            {"session_id": "session-1", "status": "running"},
        ),
    ],
    ids=["non-dict-session", "naive-started-at"],
)
def test_agents_wait_omits_unreadable_output_activity_on_timeout(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    sessions: list[object],
    expected: dict[str, str],
) -> None:
    """解釈不能な共有状態では出力活動を省略してrunning応答を返す。"""
    _write_own_status(wait_environment, sessions)

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    assert json.loads(capsys.readouterr().out) == expected


def test_agents_wait_returns_stall_notice_after_threshold(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """長時間出力されないsessionは待機上限応答へ停滞候補を付ける。"""
    _write_own_status(
        wait_environment,
        [{"session_id": "session-1", "started_at": "2000-01-01T00:00:00+00:00", "output_updated_at": None}],
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert response["output_updated_at"] is None
    assert response["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS


def test_agents_wait_keeps_waiting_after_stall_threshold(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """停滞候補の診断後も待機を続け、次の周回で終端結果を回収する。"""
    _write_own_status(wait_environment, [{"session_id": "session-1", "updated_at": "2000-01-01T00:00:00+00:00"}])
    monotonic_values = iter((0.0, 1.0, state.STALL_NOTICE_SECONDS + 1.0))
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", state.STALL_NOTICE_SECONDS + 100.0)
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: next(monotonic_values))

    monkeypatch.setattr(agents_wait.time, "sleep", _publish_result_on_sleep(wait_environment))

    assert _wait_for_session_1_result(wait_environment) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-1"}


def test_agents_wait_notices_response_reports_seconds_since_output(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """通知だけを回収する非終端応答にも活動時刻と最新テキスト出力時刻を付ける。"""
    output_updated_at = "2026-09-09T00:00:00+00:00"
    updated_at = "2099-01-01T00:00:00+00:00"
    _write_own_status(
        wait_environment,
        [{"session_id": "session-1", "output_updated_at": output_updated_at, "updated_at": updated_at}],
    )
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": output_updated_at, "body": "通知"}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert response["output_updated_at"] == output_updated_at
    assert response["updated_at"] == updated_at


def test_agents_wait_stall_uses_activity_not_text_output(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """テキスト出力が閾値を超えて止まっていても、活動が続く間は停滞の印を付けない。"""
    _write_own_status(
        wait_environment,
        [
            {
                "session_id": "session-1",
                "started_at": "2000-01-01T00:00:00+00:00",
                "output_updated_at": "2000-01-01T00:00:00+00:00",
                "updated_at": "2099-01-01T00:00:00+00:00",
            }
        ],
    )

    with pytest.raises(SystemExit, match="3"):
        atk.main(["agents", "wait"])

    response = json.loads(capsys.readouterr().out)
    assert response["seconds_since_output"] >= state.STALL_NOTICE_SECONDS
    assert response["seconds_since_activity"] == 0


def test_agents_wait_adds_notices_to_terminal_result(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じ周回の通知を終端結果へ加える。"""
    wait_environment.mkdir(parents=True)
    payload = {"session_id": "session-1", "status": "completed", "turn_seq": 2}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")
    notices = wait_environment.parent / "notices"
    notices.mkdir()
    notice = {
        "version": 1,
        "session_id": "session-1",
        "sent_at": "2026-09-07T00:00:01+00:00",
        "body": "終端時の通知",
    }
    (notices / "session-1.1.json").write_text(json.dumps(notice), encoding="utf-8")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "wait"])

    payload["notices"] = [{"sent_at": notice["sent_at"], "body": notice["body"]}]
    assert json.loads(capsys.readouterr().out) == payload
    assert not (wait_environment / "session-1.json").exists()
    assert not any(notices.iterdir())


def test_agents_wait_logs_targets_and_collection_without_result_body(
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """待機対象の由来と回収主体を記録し、結果本文をログへ含めない。"""
    wait_environment.mkdir(parents=True)
    payload = {"status": "completed", "agent_message": "秘密の結果本文"}
    (wait_environment / "session-1.json").write_text(json.dumps(payload), encoding="utf-8")

    assert _wait_for_session_1_result(wait_environment) == 0

    assert json.loads(capsys.readouterr().out)["agent_message"] == "秘密の結果本文"
    log_text = (wait_environment.parents[2] / "logs" / "agents-server.log").read_text(encoding="utf-8")
    assert "wait_start targets=session-1 origins=session-1:result" in log_text
    assert "collector=atk-agents-wait" in log_text
    assert "reason=collected count=1 session_ids=session-1" in log_text
    assert "秘密の結果本文" not in log_text


def test_agents_wait_collects_dynamic_target_under_owner_lock(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """書込主体の単一ロックを保持したまま追加対象を待機集合へ加える。"""
    _write_own_status(wait_environment, [{"session_id": "session-1"}])
    monkeypatch.setattr(state, "WAIT_TIMEOUT_SECONDS", 10.0)
    monotonic_values = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(agents_wait.time, "monotonic", lambda: next(monotonic_values))

    def change_targets(_seconds: float) -> None:
        _write_own_status(wait_environment, [{"session_id": "session-1"}, {"session_id": "session-2"}])
        wait_environment.mkdir(parents=True, exist_ok=True)
        (wait_environment / "session-2.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    monkeypatch.setattr(agents_wait.time, "sleep", change_targets)
    assert _wait_for_session_1_result(wait_environment) == 0

    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "session-2"}
    log_text = (wait_environment.parents[2] / "logs" / "agents-server.log").read_text(encoding="utf-8")
    assert "wait_target_added session_id=session-2" in log_text
