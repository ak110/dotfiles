"""`atk wi process-loop`の待機中の変更の監視のテスト。"""

import collections
import contextlib
import json
import os
import pathlib
import select
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator
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
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.process_loop_test import _start_entry
from agent_toolkit._common import process_tree, session_activity, session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands
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
        assert "status`で同期状態を確認する" in next_action

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
        _submission_cli("end", "--session-id", "submitter", "--abort")
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
def test_enter_releases_start_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    reason: str,
) -> None:
    """活動猶予・UWI猶予・両者の併存で、期限を待たずにEnterから候補の再評価と起動へ進む。"""
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setattr(_pl_watch.time, "time", lambda: 1000.0)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: reason != "uwi")
    console = _Console()
    _use_windows_console(monkeypatch, console)
    monkeypatch.setattr(_pl_watch, "wait_for_changes", _enter_then_stop(console))
    _setup_notes(tmp_path)
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    repositories = all_repos_test._repositories(tmp_path, monkeypatch)  # pylint: disable=protected-access
    target = repositories[0].repo_id
    monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: None)
    launched_calls: list[object] = []

    def run_round(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        launched_calls.append(object())
        raise KeyboardInterrupt

    monkeypatch.setattr("agent_toolkit._atk.wi.process_loop.run_coordinator", run_round)
    launched = launched_calls.copy
    command = ["wi", "process-loop", "--no-update", "--no-alerts"]
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
    repo = git_repository.init_repository(tmp_path / "repo", origin="https://github.com/example/myrepo.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(repo)]))
    monkeypatch.setattr(_pl_watch.time, "time", lambda: 1000.0)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: True)
    console = _Console()
    _use_windows_console(monkeypatch, console)
    children: list[dict[str, Any]] = []

    def unexpected_session(*_args: Any, **kwargs: Any) -> bool:
        children.append(kwargs)
        raise AssertionError("開始条件を満たしていない対象を起動した")

    monkeypatch.setattr(_pl_session, "run_process_session", unexpected_session)
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
        atk.main(["wi", "process-loop", "--no-update", "--no-alerts"], home=tmp_path)
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


@contextlib.contextmanager
def _start_wait_process(tmp_path: pathlib.Path, mode: str, grace: float, stdin: Any) -> Generator[subprocess.Popen[bytes]]:
    """開始前待機を別プロセスで動かし、待機の後に同じ標準入力を後段として読む。"""
    script = tmp_path / "start_wait.py"
    script.write_text(_START_WAIT_SCRIPT, encoding="utf-8")
    notes = _setup_notes(tmp_path)
    env = {**os.environ, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "PYTHONPATH": str(pathlib.Path(atk.__file__).parents[1])}
    with process_tree.process_scope(
        [sys.executable, str(script), mode, str(notes), str(grace)],
        stdin=stdin,
        stdout=stdin if isinstance(stdin, int) else subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    ) as process:
        yield process


@contextlib.contextmanager
def _pseudo_terminal() -> Generator[tuple[int, int, Callable[[], None]]]:
    """両端を獲得直後から所有し、子へ渡したslaveだけを早期解放できるようにする。"""
    with contextlib.ExitStack() as master_owner, contextlib.ExitStack() as slave_owner:
        master, slave = pty.openpty()
        master_owner.callback(os.close, master)
        slave_owner.callback(os.close, slave)
        yield master, slave, slave_owner.close


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
    with (
        _pseudo_terminal() as (master, slave, release_slave),
        _start_wait_process(tmp_path, "ctty", 600.0, slave) as process,
    ):
        release_slave()
        buffer = bytearray()
        _read_until(master, "Enterで今回の待機を解除", buffer)
        os.write(master, b"\n")
        _read_until(master, "result=True", buffer)
        os.write(master, b"child-input\n")
        _read_until(master, "after:'child-input\\n'", buffer)
        assert process.wait(timeout=60) == 0
    output = buffer.decode(errors="replace")
    assert "Enterの入力により開始前の待機を解除しました" in output
    assert any("event=start_wait_released trigger=enter" in line for line in _log_lines())


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末で前景TTYを作成できる")
@pytest.mark.parametrize("mode", ["early", "twice"])
def test_enter_does_not_carry_over_to_next_start_wait(tmp_path: pathlib.Path, mode: str) -> None:
    """待機の外で届いたEnterと、1回の待機中の重ね押しの2回目を、次の開始前待機の解除に使わない。"""
    grace = 2.0
    with (
        _pseudo_terminal() as (master, slave, release_slave),
        _start_wait_process(tmp_path, f"ctty-{mode}", grace, slave) as process,
    ):
        release_slave()
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
        with _start_wait_process(tmp_path, "pipe", grace, subprocess.PIPE) as process:
            stdout, _ = process.communicate(b"\n", timeout=60)
            output = stdout.decode(errors="replace")
            assert "after:'\\n'" in output
    else:
        with _pseudo_terminal() as (master, slave, release_slave):
            script_mode = {"eof": "ctty", "closed": "closed", "no-controlling-terminal": "noctty", "background": "background"}
            with _start_wait_process(tmp_path, script_mode[mode], grace, slave) as process:
                release_slave()
                buffer = bytearray()
                _read_until(master, "開始前に待機します", buffer)
                # EOFは端末の行頭でのVEOF（Ctrl-D）として送る。
                os.write(master, b"\x04" if mode == "eof" else b"\n")
                _read_until(master, "result=True", buffer)
                if mode == "eof":
                    os.write(master, b"\n")
                _read_until(master, "after:'\\n'", buffer)
                assert process.wait(timeout=60) == 0
        output = buffer.decode(errors="replace")
    assert "待機条件が解除されました" in output
    assert "Enterの入力により" not in output
    assert ("Enterで今回の待機を解除" in output) is (mode == "eof")
    elapsed = float(output.split("elapsed=", 1)[1].split()[0])
    assert elapsed >= grace - 0.5


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


def test_start_gate_extends_only_for_target_arrivals(tmp_path: pathlib.Path) -> None:
    """対象の活動中に、対象外の到着では猶予が延びず、対象の到着だけで延びる。期限の直前・到達時・到達後と活動終了も比べる。"""
    notes = _setup_notes(tmp_path)
    targets = {"https://github.com/Example/myrepo.git"}
    gate = _pl_watch.StartGate(notes)
    first = gate.evaluate(targets, activity=True, now=1000.0)
    assert first.deadline == 1600.0
    assert not first.extended
    # 対象外リポジトリへの新規登録は、対象の投入を待つ理由にならない。
    _start_entry(notes, "foreign.md", repo="github.com/example/other", kind="awi")
    foreign = gate.evaluate(targets, activity=True, now=1500.0)
    assert (foreign.deadline, foreign.extended) == (1600.0, False)
    # 対象への新規登録は猶予を延長し、同じ項目の編集は到着に数えない。
    arrival = _start_entry(notes, "arrival.md", kind="awi")
    extended = gate.evaluate(targets, activity=True, now=1550.0)
    assert (extended.deadline, extended.extended) == (2150.0, True)
    arrival.write_text(arrival.read_text(encoding="utf-8") + "\n追記\n", encoding="utf-8")
    edited = gate.evaluate(targets, activity=True, now=1560.0)
    assert (edited.deadline, edited.extended) == (2150.0, False)
    assert gate.evaluate(targets, activity=True, now=2149.0).reasons
    assert not gate.evaluate(targets, activity=True, now=2150.0).reasons
    assert not gate.evaluate(targets, activity=True, now=2200.0).reasons
    # 活動が終わっていれば、対象の到着があっても待機理由にならない。
    gate.started()
    gate.evaluate(targets, activity=True, now=3000.0)
    _start_entry(notes, "after.md", kind="awi")
    assert not gate.evaluate(targets, activity=False, now=3100.0).reasons


class _ContinuousChanges:
    """監視開始後のすべての監視に、`.md`の変更イベントを一定間隔で上限回数まで届ける。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: pathlib.Path, *, interval: float, count: int) -> None:
        self._handlers: list[watchdog.events.FileSystemEventHandler] = []
        self._path = path
        self._interval = interval
        self._count = count
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
            time.sleep(self._interval)

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
    changes = _ContinuousChanges(monkeypatch, notes / "entry.md", interval=0.05, count=200)

    def release() -> None:
        console.keys.append("\r")
        aborted[0] = True

    released = threading.Timer(0.3, release)
    changes.start()
    released.start()
    started = time.monotonic()
    assert not _pl_watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, _pl_watch.StartGate(notes))
    assert changes.running
    assert time.monotonic() - started < 5.0
    assert "Enterの入力により" not in capsys.readouterr().out
    released.join()
    changes.join()
