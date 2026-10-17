"""`atk wi process-loop`の待機中の変更の監視のテスト。"""

import collections
import os
import pathlib
import select
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

# Windowsでは読み込めないため、POSIXだけで実行するテストが使う時点で読み込む。
lazy import pty

import pytest
import watchdog.events
import watchdog.observers

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop_all_repos_test as all_repos_test
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log as _pl_log
from agent_toolkit._atk.wi import process_loop_ownership as _pl_ownership
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.process_loop_test import _start_entry
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


class _Console:
    """`msvcrt`の`kbhit`・`getwch`と同じ契約で、対話コンソールへ入力済みのキーを渡す。"""

    def __init__(self, keys: str = "") -> None:
        self.keys = collections.deque(keys)

    def kbhit(self) -> bool:
        return bool(self.keys)

    def getwch(self) -> str:
        return self.keys.popleft()


class _ConsoleStdin:
    """Windowsの対話コンソールに接続した標準入力。"""

    def isatty(self) -> bool:
        return True


def _use_windows_console(monkeypatch: pytest.MonkeyPatch, console: _Console) -> None:
    monkeypatch.setattr(_pl_watch, "_INPUT_PLATFORM", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", console)
    monkeypatch.setattr(_pl_watch.sys, "stdin", _ConsoleStdin())


def _log_lines() -> list[str]:
    path = _pl_log.log_path()
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _enter_then_stop(console: _Console) -> Callable[..., bool]:
    """最初の開始前待機の間にEnterを入力し、子の起動後の待機で常駐を止める。"""
    calls: list[dict[str, Any]] = []

    def wait(*_args: Any, **kwargs: Any) -> bool:
        calls.append(kwargs)
        interrupt = kwargs.get("interrupt")
        if len(calls) > 1 or interrupt is None:
            raise KeyboardInterrupt
        assert not interrupt()
        console.keys.extend("\r")
        assert interrupt()
        return True

    return wait


@pytest.mark.parametrize("reason", ["activity", "uwi", "both"])
@pytest.mark.parametrize("entry", ["single", "all-repos"])
def test_enter_releases_start_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    reason: str,
    entry: str,
) -> None:
    """活動猶予・UWI猶予・両者の併存で、期限を待たずにEnterから候補の再評価と起動へ進む。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setattr(_pl_watch.time, "time", lambda: 1000.0)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: reason != "uwi")
    console = _Console()
    _use_windows_console(monkeypatch, console)
    monkeypatch.setattr(_pl_watch, "wait_for_changes", _enter_then_stop(console))
    if entry == "single":
        notes = _setup_notes(tmp_path)
        target = "github.com/example/myrepo"
        repo = tmp_path / "repo"
        repo.mkdir()
        children: list[dict[str, Any]] = []
        fake_run = fake_run_with_remote_url(repo, children, 0)

        def run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            result = fake_run(cmd, *args, **kwargs)
            if children:
                raise KeyboardInterrupt
            return result

        monkeypatch.setattr(subprocess, "run", run)
        command = ["wi", "process-loop", "--target-repo", str(repo), "--no-update", "--no-alerts"]
        launched = children.copy
    else:
        _setup_notes(tmp_path)
        notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
        repositories = all_repos_test._repositories(tmp_path, monkeypatch)  # pylint: disable=protected-access  # noqa: SLF001
        target = repositories[0].repo_id
        manager = all_repos_test._Manager()  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: None)
        monkeypatch.setattr("agent_toolkit.atk._agents_manager.AgentsServerManager", lambda **_kwargs: manager)
        launched = manager.started.copy
        command = ["wi", "process-loop", "--no-update", "--no-alerts", "all-repos"]
    _start_entry(notes, "ready.md", repo=target, kind="awi")
    if reason != "activity":
        _start_entry(notes, "question.md", repo=target)
    with pytest.raises(SystemExit) as exc:
        atk.main(command, home=tmp_path)
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "Enterで今回の待機を解除" in output
    assert "Enterの入力により開始前の待機を解除しました" in output
    assert ("WI投入作業が活動中" in output) is (reason != "uwi")
    assert ("未回答UWIが1件" in output) is (reason != "activity")
    assert len(launched()) == 1
    assert any("event=start_wait_released trigger=enter" in line for line in _log_lines())


def test_start_wait_without_enter_and_no_carry_over(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Enterが無ければ自然解除・延長・期限解除で開始へ進み、Enterの解除は次の待機へ残らない。"""
    notes = _setup_notes(tmp_path)
    targets = {"github.com/example/myrepo"}
    clock = [1000.0]
    active = [True]
    monkeypatch.setattr(_pl_watch.time, "time", lambda: clock[0])
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: active[0])
    console = _Console()
    _use_windows_console(monkeypatch, console)
    steps: list[Callable[[], None]] = []

    def wait(*_args: Any, interrupt: Callable[[], bool] | None = None, **_kwargs: Any) -> bool:
        assert interrupt is not None
        assert not interrupt()
        steps.pop(0)()
        return interrupt()

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    gate = _pl_watch.StartGate(notes)

    def arrive() -> None:
        clock[0] = 1500.0
        _start_entry(notes, "arrival.md", kind="awi", timestamp=1500.0)

    def expire() -> None:
        clock[0] = 2100.0

    # 延長と期限解除
    steps.extend([arrive, expire])
    assert _pl_watch.wait_for_start_permission(notes, targets, gate)
    output = capsys.readouterr().out
    assert "新規WIの到着により延長" in output and "待機条件が解除されました" in output
    # 自然解除: 活動の終了で期限前に解除する
    gate.started()

    def finish() -> None:
        active[0] = False

    steps.append(finish)
    assert _pl_watch.wait_for_start_permission(notes, targets, gate)
    assert "待機条件が解除されました" in capsys.readouterr().out
    # Enterで解除した次の待機は、Enterが無ければ期限まで待つ。
    active[0] = True
    gate.started()

    def enter() -> None:
        console.keys.append("\r")

    steps.append(enter)
    assert _pl_watch.wait_for_start_permission(notes, targets, gate)
    assert "Enterの入力により" in capsys.readouterr().out
    clock[0] = 2200.0

    def late_expire() -> None:
        clock[0] = 2800.0

    steps.extend([lambda: None, late_expire])
    assert _pl_watch.wait_for_start_permission(notes, targets, gate)
    output = capsys.readouterr().out
    assert "Enterの入力により" not in output and "待機条件が解除されました" in output
    assert not steps


def test_enter_on_windows_console(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """対話コンソールの境界で、案内の後のEnterだけで解除し、前後に溜まったキーを破棄してログへ残す。

    監視の開始前に届いていたEnterでは解除せず、解除に使ったEnterの後に続くキーも次回の待機へ残さない。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: True)
    console = _Console("\r")
    _use_windows_console(monkeypatch, console)
    notes = _setup_notes(tmp_path)
    waits: list[list[str]] = []

    def wait(*_args: Any, interrupt: Callable[[], bool], **_kwargs: Any) -> bool:
        waits.append(list(console.keys))
        console.keys.extend("a\r\r")
        return interrupt()

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    assert _pl_watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, _pl_watch.StartGate(notes))
    assert waits == [[]]
    output = capsys.readouterr().out
    assert "Enterで今回の待機を解除" in output and "Enterの入力により" in output
    assert not console.keys
    assert any("event=start_wait_released trigger=enter" in line for line in _log_lines())


@pytest.mark.parametrize("case", ["candidate-removed", "owner-busy", "prepare-and-sync-failed"])
def test_enter_release_keeps_start_conditions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    """Enter解除後も候補・所有・準備・同期の既存の起動条件を通り、その再試行の待機はEnterで解除しない。"""
    notes = _setup_notes(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(_pl_watch.time, "time", lambda: 1000.0)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: True)
    console = _Console()
    _use_windows_console(monkeypatch, console)
    children: list[dict[str, Any]] = []
    monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(repo, children, 0))
    ready = _start_entry(notes, "ready.md", kind="awi")
    pulls: list[bool] = []

    def pull(_path: pathlib.Path) -> bool:
        pulls.append(True)
        return len(pulls) == 1

    if case == "owner-busy":
        monkeypatch.setattr(_pl_ownership, "try_acquire", lambda *_args, **_kwargs: None)
    elif case == "prepare-and-sync-failed":
        monkeypatch.setattr(_pl_worktree, "prepare_session_target", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(_pl_watch, "pull_private_notes", pull)
    waits: list[dict[str, Any]] = []
    stop_at = 3 if case == "prepare-and-sync-failed" else 2

    def wait(*_args: Any, **kwargs: Any) -> bool:
        waits.append(kwargs)
        if len(waits) == 1:
            console.keys.append("\r")
            if case == "candidate-removed":
                ready.unlink()
            return kwargs["interrupt"]()
        assert kwargs.get("interrupt") is None
        if len(waits) == stop_at:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "process-loop", "--target-repo", str(repo), "--no-update", "--no-alerts"], home=tmp_path)
    assert exc.value.code == 0
    assert len(waits) == stop_at
    assert not children
    output = capsys.readouterr().out
    assert "Enterの入力により" in output
    expected = {
        "candidate-removed": "0件のため変更検知を待機します。",
        "owner-busy": "別のprocess-loopが処理中です",
        "prepare-and-sync-failed": "同期を再試行するまで変更検知を待機します。",
    }[case]
    assert expected in output


def test_abort_precedes_enter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """中断要求とEnterが同じ待機で重なった場合は、開始せず終了を優先する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: True)
    console = _Console()
    _use_windows_console(monkeypatch, console)

    def wait(*_args: Any, interrupt: Callable[[], bool], **_kwargs: Any) -> bool:
        console.keys.append("\r")
        with pytest.raises(SystemExit) as abort_exit:
            atk.main(["wi", "process-loop", "abort"], home=tmp_path)
        assert abort_exit.value.code == 0
        return interrupt()

    monkeypatch.setattr(_pl_watch, "wait_for_changes", wait)
    assert not _pl_watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, _pl_watch.StartGate(notes))
    assert "Enterの入力により" not in capsys.readouterr().out


_START_WAIT_SCRIPT = """
import os, pathlib, subprocess, sys, time
mode, notes, grace = sys.argv[1], pathlib.Path(sys.argv[2]), float(sys.argv[3])
if mode in ("ctty", "background", "closed", "ctty-early", "ctty-twice"):
    # start_new_session後に端末を開き、前景のプロセスグループが持つ制御端末にする。
    os.close(os.open(os.ttyname(0), os.O_RDWR))
if mode == "background":
    # 同じ端末の別プロセスグループで待機させ、このプロセスは前景に残る。
    subprocess.run([sys.executable, __file__, "plain", str(notes), str(grace)], process_group=0, check=True)
    print("after:" + repr(sys.stdin.readline()), flush=True)
    raise SystemExit(0)
if mode == "closed":
    stdin, sys.stdin = sys.stdin, open(os.devnull, encoding="utf-8")
    sys.stdin.close()
from agent_toolkit._atk.wi import process_loop_watch as watch
watch._START_GRACE_SEC = grace
watch._submission_active = lambda _targets: True
if mode == "ctty-early":
    # 開始前待機の外で端末へ届いたEnterを、次の開始前待機の解除に使わないことを確かめる。
    print("ready", flush=True)
    time.sleep(2)
rounds = 2 if mode == "ctty-twice" else 1
for _ in range(rounds):
    started = time.monotonic()
    result = watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, watch.StartGate(notes))
    print(f"result={result} elapsed={time.monotonic() - started:.1f}", flush=True)
if mode in ("ctty-early", "ctty-twice"):
    print("waits-done", flush=True)
if mode == "closed":
    sys.stdin = stdin
if mode != "plain":
    print("after:" + repr(sys.stdin.readline()), flush=True)
"""


def _start_wait_process(tmp_path: pathlib.Path, mode: str, grace: float, stdin: Any) -> subprocess.Popen[bytes]:
    """開始前待機を別プロセスで動かし、待機の後に同じ標準入力を後段として読む。"""
    script = tmp_path / "start_wait.py"
    script.write_text(_START_WAIT_SCRIPT, encoding="utf-8")
    notes = _setup_notes(tmp_path)
    env = {**os.environ, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "PYTHONPATH": str(pathlib.Path(atk.__file__).parents[1])}
    return subprocess.Popen(
        [sys.executable, str(script), mode, str(notes), str(grace)],
        stdin=stdin,
        stdout=stdin if isinstance(stdin, int) else subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )


def _read_until(master: int, marker: str, buffer: bytearray, *, timeout: float = 60.0) -> None:
    """PTYの出力に`marker`が現れるまで読む。上限までに現れなければ読んだ出力とともに失敗する。"""
    end = time.monotonic() + timeout
    while marker.encode() not in buffer:
        remaining = end - time.monotonic()
        assert remaining > 0, buffer.decode(errors="replace")
        readable, _, _ = select.select([master], [], [], remaining)
        if not readable:
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError:
            chunk = b""
        assert chunk, buffer.decode(errors="replace")
        buffer.extend(chunk)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末でだけ前景TTYを作成できる")
def test_enter_on_posix_pty(tmp_path: pathlib.Path) -> None:
    """前景TTYでは案内を表示し、Enterで解除してログへ残し、後の入力を後段へ渡す。"""
    master, slave = pty.openpty()
    try:
        process = _start_wait_process(tmp_path, "ctty", 600.0, slave)
        os.close(slave)
        buffer = bytearray()
        _read_until(master, "Enterで今回の待機を解除", buffer)
        os.write(master, b"\n")
        _read_until(master, "result=True", buffer)
        os.write(master, b"child-input\n")
        _read_until(master, "after:'child-input\\n'", buffer)
        assert process.wait(timeout=60) == 0
    finally:
        os.close(master)
    output = buffer.decode(errors="replace")
    assert "Enterの入力により開始前の待機を解除しました" in output
    assert any("event=start_wait_released trigger=enter" in line for line in _log_lines())


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末で前景TTYを作成できる")
@pytest.mark.parametrize("mode", ["early", "twice"])
def test_enter_does_not_carry_over_to_next_start_wait(tmp_path: pathlib.Path, mode: str) -> None:
    """待機の外で届いたEnterと、1回の待機中の重ね押しの2回目を、次の開始前待機の解除に使わない。"""
    grace = 2.0
    master, slave = pty.openpty()
    try:
        process = _start_wait_process(tmp_path, f"ctty-{mode}", grace, slave)
        os.close(slave)
        buffer = bytearray()
        if mode == "early":
            _read_until(master, "ready", buffer)
            os.write(master, b"\n")
        else:
            _read_until(master, "Enterで今回の待機を解除", buffer)
            os.write(master, b"\n\n")
            _read_until(master, "result=True", buffer)
        _read_until(master, "待機条件が解除されました", buffer)
        # 全ての待機の結果行を出力し終えた印を読んでから後段の入力を書く。先に書くと端末のエコーが結果行へ混ざる。
        _read_until(master, "waits-done", buffer)
        os.write(master, b"child-input\n")
        _read_until(master, "after:'child-input\\n'", buffer)
        assert process.wait(timeout=60) == 0
    finally:
        os.close(master)
    output = buffer.decode(errors="replace")
    assert output.count("Enterの入力により") == (1 if mode == "twice" else 0)
    elapsed = [float(part.split()[0]) for part in output.split("elapsed=")[1:]]
    assert elapsed[-1] >= grace - 0.5


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末とプロセスグループで入力の境界を作成する")
@pytest.mark.parametrize("mode", ["pipe", "eof", "closed", "no-controlling-terminal", "background"])
def test_non_interactive_input_keeps_automatic_wait(tmp_path: pathlib.Path, mode: str) -> None:
    """非TTY・EOF・閉鎖済み・入力不能・バックグラウンドジョブでは入力を読まず、期限で解除して入力を後段へ残す。"""
    grace = 2.0
    if mode == "pipe":
        process = _start_wait_process(tmp_path, "pipe", grace, subprocess.PIPE)
        stdout, _ = process.communicate(b"\n", timeout=60)
        output = stdout.decode(errors="replace")
        assert "after:'\\n'" in output
    else:
        master, slave = pty.openpty()
        try:
            script_mode = {"eof": "ctty", "closed": "closed", "no-controlling-terminal": "noctty", "background": "background"}
            process = _start_wait_process(tmp_path, script_mode[mode], grace, slave)
            os.close(slave)
            buffer = bytearray()
            _read_until(master, "開始前に待機します", buffer)
            # EOFは端末の行頭でのVEOF（Ctrl-D）として送る。
            os.write(master, b"\x04" if mode == "eof" else b"\n")
            _read_until(master, "result=True", buffer)
            if mode == "eof":
                os.write(master, b"\n")
            _read_until(master, "after:'\\n'", buffer)
            assert process.wait(timeout=60) == 0
        finally:
            os.close(master)
        output = buffer.decode(errors="replace")
    assert "待機条件が解除されました" in output
    assert "Enterの入力により" not in output
    assert ("Enterで今回の待機を解除" in output) is (mode == "eof")
    elapsed = float(output.split("elapsed=", 1)[1].split()[0])
    assert elapsed >= grace - 0.5
