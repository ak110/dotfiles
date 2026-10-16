"""`atk wi process-loop`の待機中の変更の監視のテスト。"""

import os
import pathlib
import subprocess
import threading
from collections.abc import Callable
from typing import Any

import pytest
import watchdog.events
import watchdog.observers

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log as _pl_log
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._common import session_activity, session_registry
from agent_toolkit._testing.process_loop_support import fake_run_with_remote_url, isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes

_SUBMISSION_ACTIVE = _pl_watch._submission_active  # pylint: disable=protected-access  # noqa: SLF001
"""共通の隔離が無活動へ差し替える前の、実際の活動取得。"""


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


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
        (private_notes / "processing").mkdir(parents=True)
        (private_notes / "inbox").mkdir(parents=True)
        return private_notes

    @staticmethod
    def _use_synchronous_observer(
        monkeypatch: pytest.MonkeyPatch,
        initial_event_path: pathlib.Path,
    ) -> Callable[[pathlib.Path], None]:
        observers: list[Any] = []

        class SynchronousObserver:
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
                self._handlers[path.parent].on_any_event(watchdog.events.FileCreatedEvent(str(path)))

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

    def test_missing_inbox_dirs_are_created(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """inboxディレクトリ未作成でも監視前に作成され、タイムアウト時の処理が動作すること。"""
        private_notes = tmp_path / "private-notes"
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.1)
        monkeypatch.setattr(_pl_watch, "_DEBOUNCE_SEC", 0.1)
        pull_calls: list[pathlib.Path] = []
        monkeypatch.setattr(_wi_sync, "pull", pull_calls.append)

        _pl_watch.wait_for_changes(private_notes, None)  # pylint: disable=protected-access  # noqa: SLF001

        assert (private_notes / "processing").is_dir()
        assert (private_notes / "inbox").is_dir()
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
        assert "status`で同期状態を確認する" in next_action

    def test_change_event_skips_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """タイムアウト前に`.md`ファイル変更を検知した場合、`pull`が呼ばれないこと。"""
        private_notes = self._make_private_notes(tmp_path)
        inbox = private_notes / "inbox"
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
        inbox = private_notes / "inbox"
        entry1 = inbox / "entry1.md"
        entry2 = inbox / "entry2.md"
        emit = self._use_synchronous_observer(monkeypatch, entry1)
        monkeypatch.setattr(_pl_watch, "_POLL_INTERVAL_SEC", 0.2)
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
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(
            _wi_readiness,
            "count_pending_entries",
            lambda *_a, **_kw: 0,
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )
        captured = capsys.readouterr()
        assert "0件のため変更検知を待機します。" in captured.out


@pytest.mark.parametrize("event", ["deadline", "activity-ended", "abort"])
def test_start_wait_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    event: str,
) -> None:
    """開始待機が期限・活動終了・中断で復帰し、理由と期限を端末とログへ渡す。"""
    notes = _setup_notes(tmp_path)
    clock = [1000.0]
    active = [True]
    aborted = [False]
    records: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(_pl_watch.time, "time", lambda: clock[0])
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: active[0])
    monkeypatch.setattr(_pl_control, "consume_process_loop_abort", lambda: aborted[0])

    def log(name: str, **fields: Any) -> None:
        records.append((name, fields))

    monkeypatch.setattr(_pl_log, "append", log)
    waits: list[float] = []

    def wait(_notes: pathlib.Path, _repo: str | None, *, timeout: float, **_kwargs: Any) -> bool:
        waits.append(timeout)
        assert 0 < timeout <= 600
        assert len(waits) == 1
        if event == "deadline":
            clock[0] = 1600
        elif event == "activity-ended":
            active[0] = False
        else:
            aborted[0] = True
        return False

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    result = _pl_watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, _pl_watch.StartGate(notes))
    assert result is (event != "abort")
    assert len(waits) == 1
    assert records[0][0] == "start_wait"
    assert records[0][1]["deadline"] == 1600
    assert records[0][1]["reasons"]
    output = capsys.readouterr().out
    assert "活動" in output and "期限" in output
    assert any(name == "start_wait_released" for name, _ in records) is (event != "abort")


def _submission_cli(*arguments: str) -> None:
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "submission", *arguments])
    assert exc.value.code == 0


@pytest.mark.parametrize(
    "targets",
    [{"github.com/example/myrepo"}, {"github.com/example/a", "github.com/example/myrepo"}],
    ids=["single", "all-repos"],
)
def test_start_gate_waits_only_for_add_awi_by_user_to_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    targets: set[str],
) -> None:
    """対象リポジトリ集合へ投入する実行中のadd-awi-by-userだけが猶予を生み、終了で解除する。"""
    monkeypatch.setattr(_pl_watch, "_submission_active", _SUBMISSION_ACTIVE)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    notes = _setup_notes(tmp_path)
    # 自ループの子孫は活動から除くため、テストの外側で生存する親プロセスを会話のホストとする。
    identity = session_registry.ProcessIdentity.of(os.getppid())
    assert identity is not None
    host = session_activity.HostProcess("claude", identity)
    for session_id in ("submitter", "foreign", "collaboration"):
        session_activity.record_activity({"session_id": session_id, "hook_event_name": "UserPromptSubmit"}, process=host)
    _submission_cli("begin", "--session-id", "foreign", "--target-repo", "github.com/example/foreign")
    unrelated = {"github.com/example/a"}
    assert _pl_watch.wait_for_start_permission(notes, unrelated, _pl_watch.StartGate(notes))

    _submission_cli("begin", "--session-id", "submitter", "--target-repo", "https://github.com/Example/myrepo.git")
    waits: list[float] = []

    def wait(_notes: pathlib.Path, _repo: str | None, *, timeout: float, **_kwargs: Any) -> bool:
        waits.append(timeout)
        _submission_cli("end", "--session-id", "submitter")
        return True

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    capsys.readouterr()
    assert _pl_watch.wait_for_start_permission(notes, targets, _pl_watch.StartGate(notes))
    assert len(waits) == 1
    output = capsys.readouterr().out
    assert "WI投入作業が活動中" in output
    assert "待機条件が解除されました" in output
