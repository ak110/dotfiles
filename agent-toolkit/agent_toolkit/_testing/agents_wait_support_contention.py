"""待機CLI試験のロック競合とスレッド回収の補助。"""

import json
import pathlib
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, notice_inbox, retained_results, shared_layout
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._testing.agents_wait_support import _write_own_status


def _wait_lock_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """root書込主体の待機所有権を表すロックファイルのパスを返す。"""
    return shared_layout.status_directory("root-session", tmp_path) / "wait-locks" / "root.json.lock"


def _write_current_wait_run(
    tmp_path: pathlib.Path,
    *,
    target: str,
    status: str,
    continuable: bool = False,
) -> tuple[pathlib.Path, pathlib.Path]:
    """指定したrunをcurrentとして保存し、runディレクトリと記録ファイルのパスを返す。"""
    run_directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
    run_path = run_directory / "run-1.json"
    value: dict[str, Any] = {
        "run_id": "run-1",
        "status": status,
        "targets": [target],
        "started_at": "2026-09-21T00:00:00+00:00",
    }
    if status != "running":
        value.update(
            continuable=continuable,
            output=f'{{"session_id":"{target}","status":"completed"}}',
            exit_code=0,
            stream="stdout",
        )
    agents_wait._write_json(  # pylint: disable=protected-access
        run_path,
        value,
    )
    agents_wait._write_json(run_directory / "current.json", {"run_id": "run-1"})  # pylint: disable=protected-access
    return run_directory, run_path


def _observe_waiters(monkeypatch: pytest.MonkeyPatch, expected_count: int) -> tuple[Callable[..., None], threading.Event]:
    """指定件数の後続waitがlock競合へ到達した時点を通知する。"""
    original_acquire = agents_wait.acquire_lock
    followers_waiting = threading.Event()
    count_lock = threading.Lock()
    failed_threads: set[int] = set()

    def observe_acquire(lock_file, *, blocking: bool):
        try:
            return original_acquire(lock_file, blocking=blocking)
        except OSError:
            if not blocking:
                with count_lock:
                    failed_threads.add(threading.get_ident())
                    if len(failed_threads) == expected_count:
                        followers_waiting.set()
            raise

    monkeypatch.setattr(agents_wait, "acquire_lock", observe_acquire)
    return original_acquire, followers_waiting


# 並行待機テストが`get_cli_wait_timeout`へ与える処理期限と、待機スレッドの終了確認期限。
# 終了確認を処理期限より先に打ち切ると、期限内に正常回復する待機を負荷の違いで失敗と判定するため、
# 終了確認期限は処理期限に余裕を加えた値とする。
_CLI_WAIT_TIMEOUT_SECONDS = 10.0


_THREAD_JOIN_SECONDS = _CLI_WAIT_TIMEOUT_SECONDS + 5.0


_CONTENDED_FOLLOWER_NAME = "contended-wait-follower"


def _join_threads(threads: list[threading.Thread], timeout: float = _THREAD_JOIN_SECONDS) -> list[str]:
    """開始済みの全スレッドを共通の期限まで待ち、期限後も生存するスレッド名を返す。

    先に待ったスレッドの生存にかかわらず残りも待つので、失敗時もテスト用の差し替えの解除前に全スレッドを回収できる。
    """
    deadline = time.monotonic() + timeout
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return [thread.name for thread in threads if thread.is_alive()]


class _LockedStepFailed(Exception):
    """lock保持中の準備・検証が失敗した状況を再現するテスト専用の例外。"""


def _followers_under_lock(
    lock_path: pathlib.Path,
    original_acquire: Callable[..., None],
    followers_waiting: threading.Event,
    follow: Callable[[], None],
    count: int,
    while_locked: Callable[[], None] | None = None,
) -> None:
    """lockを保持したまま後発待機を開始し、lock競合への到達後に`while_locked`を実行する。

    準備・検証が例外で終わっても、lockを解放して開始した全スレッドを共通の期限まで待ってから例外を伝える。
    """
    with lock_path.open("a+b") as lock_file:
        original_acquire(lock_file, blocking=False)
        followers = [threading.Thread(target=follow, name=_CONTENDED_FOLLOWER_NAME, daemon=True) for _ in range(count)]
        try:
            for follower in followers:
                follower.start()
            assert followers_waiting.wait(timeout=5)
            if while_locked is not None:
                while_locked()
        finally:
            release_lock(lock_file)
            alive = _join_threads(followers)
        assert not alive


def _alive_followers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == _CONTENDED_FOLLOWER_NAME]


def _run_contended_wait(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[pathlib.Path, pathlib.Path], None] | None = None,
) -> int:
    """後発待機がlockに並んだ後、未公開runを残して所有権を渡す。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: _CLI_WAIT_TIMEOUT_SECONDS)
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    original_acquire, follower_waiting = _observe_waiters(monkeypatch, 1)
    lock_path = _wait_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    outcomes: list[int] = []

    def follow() -> None:
        outcomes.append(
            agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path)
        )

    _followers_under_lock(
        lock_path,
        original_acquire,
        follower_waiting,
        follow,
        1,
        None if prepare is None else lambda: prepare(tmp_path, run_path),
    )
    assert len(outcomes) == 1
    return outcomes[0]


def _leave_result_original(tmp_path: pathlib.Path, _run_path: pathlib.Path) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True, exist_ok=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")


def _stash_result(tmp_path: pathlib.Path, run_path: pathlib.Path) -> None:
    _leave_result_original(tmp_path, run_path)
    claimed, error = retained_results.take_result(
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
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    assert notice_inbox.take_notices(
        "root-session",
        "session-1",
        tmp_path,
        recipient_session_id="root-session",
        owner_status_file="root.json",
        stash_directory=run_path.with_suffix("") / "notices",
    ) == [{"body": "通知"}]


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
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": own_session_id}])
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
