# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""ファイル変更の監視、期限・割込での復帰と監視資源の回収を検証する。"""

import json
import pathlib
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
import watchdog.events
import watchdog.observers

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_watch_support import _Console, _use_windows_console
from agent_toolkit._testing.process_loop_watch_support import _isolate_process_loop_commands as _isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


class TestChangeHandler:
    """_ChangeHandler.on_any_event: 監視対象イベント判定の実動作を検証する。"""

    def test_md_file_created_event_sets_change_event(self) -> None:
        """`.md`拡張子・非ディレクトリのFileCreatedEventでchange_eventがsetされること。"""
        change_event = threading.Event()
        handler = _pl_watch._ChangeHandler(change_event)  # pylint: disable=protected-access  # noqa: SLF001
        event = watchdog.events.FileCreatedEvent("/tmp/dummy/inbox/entry.md")

        handler.on_any_event(event)

        assert change_event.is_set()

    def test_directory_event_ignored(self) -> None:
        """イベント種別フィルタを通過してもディレクトリイベントは無視されること。"""
        change_event = threading.Event()
        handler = _pl_watch._ChangeHandler(change_event)  # pylint: disable=protected-access  # noqa: SLF001
        event = watchdog.events.FileCreatedEvent("/tmp/dummy/inbox/subdir")
        event.is_directory = True  # WATCHED_EVENT_TYPES判定を通過させたうえでディレクトリ判定分岐に到達させる

        handler.on_any_event(event)

        assert not change_event.is_set()

    def test_non_md_file_event_ignored(self) -> None:
        """`.md`以外の拡張子のファイルイベントは無視されchange_eventがsetされないこと。"""
        change_event = threading.Event()
        handler = _pl_watch._ChangeHandler(change_event)  # pylint: disable=protected-access  # noqa: SLF001
        event = watchdog.events.FileCreatedEvent("/tmp/dummy/inbox/entry.txt")

        handler.on_any_event(event)

        assert not change_event.is_set()


class TestWaitForChanges:
    """_wait_for_changes: watchdog監視の実動作（タイムアウト・変更検知・デバウンス）を検証する。"""

    @staticmethod
    def _make_private_notes(tmp_path: pathlib.Path) -> pathlib.Path:
        private_notes = tmp_path / "private-notes"
        (private_notes / "2026" / "10").mkdir(parents=True)
        return private_notes

    @staticmethod
    def _use_synchronous_observer(
        monkeypatch: pytest.MonkeyPatch,
        initial_event_path: pathlib.Path,
    ) -> Callable[[pathlib.Path], None]:
        observers: list[Any] = []

        class SynchronousObserver:
            ident = None

            def __init__(self) -> None:
                self._handlers: dict[pathlib.Path, watchdog.events.FileSystemEventHandler] = {}
                observers.append(self)

            def schedule(
                self,
                handler: watchdog.events.FileSystemEventHandler,
                path: str,
                *,
                recursive: bool,
            ) -> None:
                del recursive
                self._handlers[pathlib.Path(path)] = handler

            def emit(self, path: pathlib.Path) -> None:
                for directory, handler in self._handlers.items():
                    if path.is_relative_to(directory):
                        handler.on_any_event(watchdog.events.FileCreatedEvent(str(path)))

            def start(self) -> None:
                self.emit(initial_event_path)

            def stop(self) -> None:
                pass

            def join(self) -> None:
                pass

        monkeypatch.setattr(watchdog.observers, "Observer", SynchronousObserver)

        def emit(path: pathlib.Path) -> None:
            observers[0].emit(path)

        return emit

    def test_missing_private_notes_root_is_created(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """保存先が未作成でも監視前に作成され、タイムアウト時の処理が動作すること。"""
        private_notes = tmp_path / "private-notes"
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_wi_sync, "pull", pull_calls.append)

        _pl_watch.wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert private_notes.is_dir()
        assert pull_calls == [private_notes]

    def test_timeout_triggers_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """変更検知イベント無しでタイムアウトに達した場合、`pull`が呼ばれること。"""
        private_notes = self._make_private_notes(tmp_path)
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_wi_sync, "pull", pull_calls.append)

        _pl_watch.wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert pull_calls == [private_notes]

    @pytest.mark.parametrize(
        "error",
        [
            subprocess.CalledProcessError(1, ["git", "merge", "--ff-only", "@{u}"]),
            _atk_git_sync.RebaseInProgressError("rebase中"),
        ],
        ids=["git-command-failure", "rebase-in-progress"],
    )
    def test_pull_failure_is_caught_and_warned(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        error: Exception,
    ) -> None:
        """タイムアウト時の同期失敗を送出せず、stderr警告を出力して復帰する。"""
        private_notes = self._make_private_notes(tmp_path)
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.1)

        def fake_pull(_path: pathlib.Path) -> None:
            raise error

        monkeypatch.setattr(_wi_sync, "pull", fake_pull)

        _pl_watch.wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        stderr = capsys.readouterr().err
        assert "remote同期に失敗" in stderr
        # 待機を続ける旨と、繰り返す場合の確認コマンドを次の操作として続ける。
        next_action = stderr.split("\n次の操作: ", 1)[1]
        assert next_action.startswith("対応不要（待機は継続した）")
        assert f"git -C {private_notes} status" in next_action and "確認せよ" in next_action

    def test_change_event_skips_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """タイムアウト前に`.md`ファイル変更を検知した場合、`pull`が呼ばれないこと。"""
        private_notes = self._make_private_notes(tmp_path)
        inbox = private_notes / "2026" / "10"
        entry = inbox / "entry.md"
        self._use_synchronous_observer(monkeypatch, entry)
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_wi_sync, "pull", pull_calls.append)

        _pl_watch.wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert not pull_calls

    def test_debounce_folds_additional_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """デバウンス窓内の追加イベントが`clear`→`wait(timeout=_DEBOUNCE_SEC)`ループで1回の変更にまとめられること。"""
        private_notes = self._make_private_notes(tmp_path)
        inbox = private_notes / "2026" / "10"
        entry1 = inbox / "entry1.md"
        entry2 = inbox / "entry2.md"
        emit = self._use_synchronous_observer(monkeypatch, entry1)
        # 集約の待機は全体期限の残りで短くなるため、期限を集約の窓より十分長くして窓の秒数を比べる。
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 5.0)
        monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.1)
        monkeypatch.setattr(
            _wi_sync,
            "pull",
            lambda _path: pytest.fail("デバウンスの処理では_pullを呼ばないこと"),
        )

        wait_calls: list[float | None] = []
        real_wait = threading.Event.wait

        def counting_wait(self: threading.Event, timeout: float | None = None) -> bool:
            wait_calls.append(timeout)
            if timeout == 0.1 and 0.1 not in wait_calls[:-1]:
                emit(entry2)
            return real_wait(self, timeout)

        monkeypatch.setattr(threading.Event, "wait", counting_wait)

        _pl_watch.wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        debounce_waits = [t for t in wait_calls if t == 0.1]
        assert len(debounce_waits) >= 2


class TestProcessLoopWaitMessage:
    """0件検知時の待機メッセージ出力を検証する。"""

    def test_wait_message_printed_before_wait(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """0件検知時に`_wait_for_changes`呼び出し直前で待機メッセージが出力されること。"""
        myrepo = git_repository.init_repository(tmp_path / "repo", origin="https://github.com/example/myrepo.git")
        _setup_notes(tmp_path)
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(myrepo)]))

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", "--no-update", "--no-alerts"],
                home=tmp_path,
            )
        captured = capsys.readouterr()
        assert "0件のため変更検知を待機します。" in captured.out


@pytest.mark.parametrize("stage", ["prepare", "stop"])
def test_observer_scope_releases_after_preparation_or_stop_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, stage: str
) -> None:
    """監視準備の失敗でも停止し、stop失敗でも開始済みObserverのjoinへ進む。"""
    calls: list[str] = []

    class Observer:
        ident = 1

        def schedule(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("監視準備の失敗")

        def start(self) -> None:
            pass

        def stop(self) -> None:
            calls.append("stop")
            if stage == "stop":
                raise RuntimeError("監視停止の失敗")

        def join(self) -> None:
            calls.append("join")

    monkeypatch.setattr(watchdog.observers, "Observer", Observer)
    with pytest.raises(RuntimeError, match="監視"):
        if stage == "prepare":
            _pl_watch.wait_for_changes(tmp_path, "example/repo")
        else:
            with _pl_watch._watch_observer(lambda _observer: None):  # pylint: disable=protected-access
                pass
    assert calls == ["stop", "join"]


class _ContinuousChanges:
    """監視開始後のすべての監視に、`.md`の変更イベントを一定間隔で上限回数まで届ける。"""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        path: pathlib.Path,
        *,
        interval: float,
        count: int,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._handlers: list[watchdog.events.FileSystemEventHandler] = []
        self._path = path
        self._interval = interval
        self._count = count
        self._on_change = on_change
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        handlers = self._handlers
        started = self._started

        class Observer:
            ident = None

            def schedule(self, handler: watchdog.events.FileSystemEventHandler, *_args: Any, **_kwargs: Any) -> None:
                handlers.append(handler)

            def start(self) -> None:
                started.set()

            def stop(self) -> None:
                pass

            def join(self) -> None:
                pass

        monkeypatch.setattr(watchdog.observers, "Observer", Observer)

    def _run(self) -> None:
        assert self._started.wait(timeout=10)
        for _ in range(self._count):
            if self._stopped.is_set():
                return
            for handler in list(self._handlers):
                handler.on_any_event(watchdog.events.FileModifiedEvent(str(self._path)))
            if self._on_change is not None:
                self._on_change()
                self._on_change = None
            if self._stopped.wait(self._interval):
                return

    def start(self) -> None:
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def join(self) -> None:
        """判定を終えた後に変更列を打ち切り、送出スレッドの終了を待つ。"""
        self._stopped.set()
        self._thread.join(timeout=30)


@pytest.mark.parametrize("timeout", [0.5, None], ids=["start-recheck", "sync-interval"])
def test_wait_returns_by_deadline_during_continuous_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, timeout: float | None
) -> None:
    """変更が静穏期間より短い間隔で続いても、全体期限で変更検知として復帰し、同期は行わない。"""
    notes = tmp_path / "private-notes"
    changes = _ContinuousChanges(monkeypatch, notes / "entry.md", interval=0.05, count=200)
    monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.5)
    monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.2)
    pulls: list[pathlib.Path] = []
    monkeypatch.setattr(_wi_sync, "pull", pulls.append)
    changes.start()
    started = time.monotonic()
    assert _pl_watch.wait_for_changes(notes, None, timeout=timeout)
    elapsed = time.monotonic() - started
    # 変更列（約10秒）が続く間に、期限（0.5秒）を大きく超えずに戻る。
    assert changes.running
    assert elapsed < 3.0
    assert not pulls
    changes.join()


def test_wait_returns_on_interrupt_during_continuous_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """変更の集約中もEnterなどの解除要求を判定し、静穏期間を待たずに戻る。"""
    notes = tmp_path / "private-notes"
    changes = _ContinuousChanges(monkeypatch, notes / "entry.md", interval=0.05, count=200)
    monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.2)
    started = time.monotonic()

    def interrupt() -> bool:
        return time.monotonic() - started > 0.5

    changes.start()
    assert _pl_watch.wait_for_changes(notes, None, timeout=60.0, interrupt=interrupt)
    assert changes.running
    assert time.monotonic() - started < 3.0
    changes.join()


def test_wait_without_changes_skips_sync_for_start_recheck(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """開始前の短い再判定は、変更が無く期限に達しても同期しない（通常の同期間隔とは区別する）。"""
    notes = tmp_path / "private-notes"
    pulls: list[pathlib.Path] = []
    monkeypatch.setattr(_wi_sync, "pull", pulls.append)
    assert not _pl_watch.wait_for_changes(notes, None, timeout=0.1)
    assert not pulls


def test_abort_precedes_enter_during_continuous_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """連続変更の最中に中断とEnterが同時に成立した場合も、短時間で外側へ戻り、中断を優先する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: True)
    monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.2)
    console = _Console()
    _use_windows_console(monkeypatch, console)
    aborted = [False]
    monkeypatch.setattr(_pl_control, "consume_process_loop_abort", lambda: aborted[0])

    def release() -> None:
        console.keys.append("\r")
        aborted[0] = True

    changes = _ContinuousChanges(monkeypatch, notes / "entry.md", interval=0.05, count=200, on_change=release)
    changes.start()
    started = time.monotonic()
    try:
        assert not _pl_watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, _pl_watch.StartGate(notes))
        assert changes.running
        assert time.monotonic() - started < 5.0
        assert "Enterの入力により" not in capsys.readouterr().out
    finally:
        changes.join()
