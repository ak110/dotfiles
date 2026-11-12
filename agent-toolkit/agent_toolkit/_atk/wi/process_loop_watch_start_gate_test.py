# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""活動とWI到着による開始猶予、Enter解除と中断優先を検証する。"""

import os
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop_all_repos_test as all_repos_test
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log as _pl_log
from agent_toolkit._atk.wi import process_loop_ownership as _pl_ownership
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._common import session_activity, session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_coordinator_support import _start_entry
from agent_toolkit._testing.process_loop_support import set_process_loop_repositories
from agent_toolkit._testing.process_loop_watch_support import (
    _SUBMISSION_ACTIVE,
    _Console,
    _enter_then_stop,
    _log_lines,
    _use_windows_console,
)
from agent_toolkit._testing.process_loop_watch_support import _isolate_process_loop_commands as _isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


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
    set_process_loop_repositories(monkeypatch, [repo])
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
