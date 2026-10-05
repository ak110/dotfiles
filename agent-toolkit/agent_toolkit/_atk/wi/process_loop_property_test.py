"""process-loopのセッション境界を独立したイベントモデルと比較する。"""

# pylint: disable=protected-access

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import pathlib
import subprocess
import tempfile

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

import agent_toolkit.atk
from agent_toolkit._atk.wi import process_loop, process_loop_log

_CONSUME_INSTRUCTIONS_IMPL = process_loop_log.consume_instructions
_CMD_PROCESS_LOOP_IMPL = process_loop._cmd_process_loop
_EXIT_ABNORMAL_SESSION_IMPL = process_loop._exit_abnormal_session
_MODEL_INSTRUCTION_SEPARATOR = "\n---\n"
_LOOP_EVENTS = (
    "sync_fail",
    "idle",
    "instruct_idle",
    "update_fail",
    "update_restart",
    "prepare_fail",
    "child_ok",
    "child_abort",
    "child_abnormal",
    "abort",
)


@dataclasses.dataclass(frozen=True)
class _LoopSnapshot:
    """各公開ループ遷移後に比較する観測状態。"""

    launches: int
    updates: int
    preparations: int
    waits: int
    restarts: int
    update_markers_consumed: int
    instruction_consumptions: int
    pending_instructions: tuple[str, ...]
    delivered_instructions: tuple[str, ...]
    resume_launches: tuple[bool, ...]
    abort_consumptions: int
    abnormal_returncodes: tuple[int, ...]


def _reference_loop_trace(events: list[str], *, resume_requested: bool, update_marker: bool) -> tuple[list[_LoopSnapshot], int]:
    """要求仕様だけからprocess-loopの遷移列と終端codeを導く。"""
    launches = 0
    updates = 0
    preparations = 0
    waits = 0
    restarts = 0
    marker_consumptions = 0
    instruction_consumptions = 0
    pending_instructions: list[str] = []
    delivered_instructions: list[str] = []
    resume_launches: list[bool] = []
    abort_consumptions = 0
    abnormal_returncodes: list[int] = []
    trace: list[_LoopSnapshot] = []
    refresh_before_session = True
    marker_pending = update_marker
    start_new_invocation = True
    resume_pending = resume_requested
    terminal_code = 0

    def snapshot() -> None:
        trace.append(
            _LoopSnapshot(
                launches=launches,
                updates=updates,
                preparations=preparations,
                waits=waits,
                restarts=restarts,
                update_markers_consumed=marker_consumptions,
                instruction_consumptions=instruction_consumptions,
                pending_instructions=tuple(pending_instructions),
                delivered_instructions=tuple(delivered_instructions),
                resume_launches=tuple(resume_launches),
                abort_consumptions=abort_consumptions,
                abnormal_returncodes=tuple(abnormal_returncodes),
            )
        )

    for index, event in enumerate(events):
        if start_new_invocation:
            marker_consumptions += int(marker_pending)
            refresh_before_session = not marker_pending
            marker_pending = False
            start_new_invocation = False
        if event == "instruct_idle":
            pending_instructions.append(f"instruction-{index}")
        if event == "abort":
            abort_consumptions += 1
            snapshot()
            break
        if event == "sync_fail":
            waits += 1
            refresh_before_session = True
            snapshot()
            continue
        if event in {"idle", "instruct_idle"}:
            waits += 1
            refresh_before_session = True
            snapshot()
            continue
        if refresh_before_session:
            updates += 1
            if event == "update_fail":
                waits += 1
                refresh_before_session = True
                snapshot()
                continue
            if event == "update_restart":
                restarts += 1
                marker_pending = True
                start_new_invocation = True
                snapshot()
                continue
        current_resume = resume_pending
        resume_pending = False
        preparations += 1
        if event == "prepare_fail" and not current_resume:
            waits += 1
            refresh_before_session = True
            snapshot()
            continue
        launches += 1
        resume_launches.append(current_resume)
        instruction_consumptions += 1
        delivered_instructions.append(_MODEL_INSTRUCTION_SEPARATOR.join(pending_instructions))
        pending_instructions.clear()
        refresh_before_session = False
        if event == "child_abnormal":
            abnormal_returncodes.append(7)
            terminal_code = 7
            snapshot()
            break
        if event == "child_abort":
            abort_consumptions += 1
            snapshot()
            break
        restarts += 1
        start_new_invocation = True
        snapshot()
    return trace, terminal_code


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    resume_pending=st.booleans(),
    worktree_name=st.one_of(st.none(), st.sampled_from(["lane-a", "lane-b"])),
    dotfiles_target=st.booleans(),
    preparation_succeeds=st.booleans(),
)
def test_session_preparation_matches_reference_model(
    resume_pending: bool,
    worktree_name: str | None,
    dotfiles_target: bool,
    preparation_succeeds: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resumeとworktree所有権から、準備成功前に子の実行先を返さない。"""
    prepared = tmp_path / "prepared"
    calls: list[str] = []

    def sync(_path: pathlib.Path, name: str) -> pathlib.Path | None:
        calls.append(name)
        return prepared if preparation_succeeds else None

    monkeypatch.setattr(process_loop, "_sync_worktree_with_upstream", sync)
    target = process_loop._DOTFILES_REPO_ID if dotfiles_target else "github.com/example/project"
    result = process_loop._prepare_session_target(
        tmp_path,
        target,
        "original prompt",
        worktree_name=worktree_name,
        resume_pending=resume_pending,
    )
    requires_preparation = not resume_pending and (worktree_name is not None or dotfiles_target)
    assert calls == ([worktree_name or process_loop._DEFAULT_WORKTREE_NAME] if requires_preparation else [])
    if requires_preparation and not preparation_succeeds:
        assert result is None
    elif requires_preparation:
        assert result == (prepared, process_loop._build_process_loop_prompt())
    else:
        assert result == (tmp_path, "original prompt")


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@example(
    events=["sync_fail", "instruct_idle", "update_restart", "child_ok", "child_abnormal"],
    resume_requested=True,
    update_marker=False,
)
@example(events=["prepare_fail", "child_abort"], resume_requested=False, update_marker=False)
@given(
    events=st.lists(st.sampled_from(_LOOP_EVENTS), min_size=1, max_size=12),
    resume_requested=st.booleans(),
    update_marker=st.booleans(),
)
def test_process_loop_event_sequences_match_reference_model(
    events: list[str],
    resume_requested: bool,
    update_marker: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIへ一連のイベントを適用し、各遷移を独立モデルと比較する。"""
    expected_trace, expected_exit = _reference_loop_trace(
        events, resume_requested=resume_requested, update_marker=update_marker
    )
    case_root = pathlib.Path(tempfile.mkdtemp(dir=tmp_path))
    (case_root / "private-notes").mkdir()
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(case_root / "private-notes"))
    repo = case_root / "repo"
    repo.mkdir()
    state_root = case_root / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setattr(process_loop, "_resolve_local_worktree", lambda _target: repo)
    monkeypatch.setattr(process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: "github.com/example/project")
    monkeypatch.setattr(process_loop, "_resolve_dotfiles_root", lambda: case_root / "dotfiles")
    monkeypatch.setattr(process_loop, "_code_hash", lambda _path: "startup")
    monkeypatch.setattr(process_loop, "_resolve_orchestrator_specs", lambda: ["codex:model/medium"])
    monkeypatch.setattr(
        process_loop,
        "_select_available_orchestrator",
        lambda *_args, **_kwargs: ("codex", "model", "medium"),
    )
    monkeypatch.setattr(process_loop, "_child_env", lambda: {})
    monkeypatch.setattr(process_loop, "_session_env", lambda env, _orchestrator: dict(env))
    monkeypatch.setattr(process_loop, "_session_creation_flags", lambda _orchestrator: 0)
    monkeypatch.setattr(process_loop, "_reset_console", lambda: None)
    monkeypatch.setattr(process_loop._console_title, "set_console_title", lambda _title: None)
    monkeypatch.setattr(
        process_loop._console_title,
        "console_title",
        lambda _title: contextlib.nullcontext(),
    )
    monkeypatch.setattr(process_loop._process_loop_log, "append", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(process_loop, "_check_process_loop_alerts", lambda *_args: (None, 0, 0))

    launches = 0
    updates = 0
    preparations = 0
    waits = 0
    restarts = 0
    marker_consumptions = 0
    instruction_consumptions = 0
    delivered_instructions: list[str] = []
    resume_launches: list[bool] = []
    abort_consumptions = 0
    abnormal_returncodes: list[int] = []
    completed_events = 0
    current_event: str | None = None
    current_index = -1
    phase = "start"
    original_read_instructions = process_loop_log.read_instructions

    def observed_snapshot() -> _LoopSnapshot:
        return _LoopSnapshot(
            launches=launches,
            updates=updates,
            preparations=preparations,
            waits=waits,
            restarts=restarts,
            update_markers_consumed=marker_consumptions,
            instruction_consumptions=instruction_consumptions,
            pending_instructions=tuple(original_read_instructions()),
            delivered_instructions=tuple(delivered_instructions),
            resume_launches=tuple(resume_launches),
            abort_consumptions=abort_consumptions,
            abnormal_returncodes=tuple(abnormal_returncodes),
        )

    def complete_event() -> None:
        nonlocal completed_events, current_event, phase
        assert observed_snapshot() == expected_trace[completed_events]
        completed_events += 1
        current_event = None
        phase = "start"

    class RestartProcessLoop(Exception):
        """公開CLI再起動の境界をテストドライバーへ返す。"""

        def __init__(self, *, resume_consumed: bool, dotfiles_updated: bool) -> None:
            super().__init__()
            self.resume_consumed = resume_consumed
            self.dotfiles_updated = dotfiles_updated

    def consume_abort() -> bool:
        nonlocal current_event, current_index, phase, abort_consumptions
        if phase == "after_child":
            should_abort = current_event == "child_abort"
            if should_abort:
                abort_consumptions += 1
                complete_event()
            return should_abort
        assert phase == "start"
        if completed_events >= len(expected_trace):
            return True
        current_index += 1
        current_event = events[current_index]
        phase = "body"
        if current_event == "instruct_idle":
            accepted, _ = process_loop_log.append_instruction(f"instruction-{current_index}")
            assert accepted
        if current_event == "abort":
            abort_consumptions += 1
            complete_event()
            return True
        return False

    def pull_notes(_private_notes: pathlib.Path) -> bool:
        assert current_event is not None
        return current_event != "sync_fail"

    def count_pending(*_args: object, **_kwargs: object) -> int:
        assert current_event is not None
        return int(current_event not in {"idle", "instruct_idle"})

    def update_before(*_args: object, **_kwargs: object) -> tuple[bool, bool]:
        nonlocal updates, phase
        assert current_event is not None
        updates += 1
        if current_event == "update_fail":
            return False, False
        if current_event == "update_restart":
            phase = "update_restart"
            process_loop._restart_process_loop([], None, mise_refreshed=True, dotfiles_updated=True)
        return True, True

    def prepare_target(
        local_path: pathlib.Path,
        _target_repo_id: str,
        prompt: str,
        *,
        worktree_name: str | None,
        resume_pending: bool,
    ) -> tuple[pathlib.Path, str] | None:
        nonlocal preparations
        del worktree_name
        assert current_event is not None
        preparations += 1
        if current_event == "prepare_fail" and not resume_pending:
            return None
        return local_path, prompt

    def build_session_argv(*_args: object, **kwargs: object) -> tuple[list[str], None]:
        resume_launches.append(bool(kwargs["resume_pending"]))
        return ["child"], None

    def consume_instructions() -> str:
        nonlocal instruction_consumptions
        instruction_consumptions += 1
        return _CONSUME_INSTRUCTIONS_IMPL()

    def run_child(_argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal launches, phase
        assert current_event is not None
        env = kwargs["env"]
        assert isinstance(env, dict)
        launches += 1
        delivered_instructions.append(str(env.get(process_loop._PROCESS_LOOP_INSTRUCTION_ENV, "")))
        phase = "after_child"
        return subprocess.CompletedProcess(["child"], 7 if current_event == "child_abnormal" else 0)

    def restart_process_loop(*_args: object, **kwargs: object) -> None:
        nonlocal restarts
        restarts += 1
        dotfiles_updated = bool(kwargs.get("dotfiles_updated", False))
        resume_consumed = bool(kwargs.get("resume_consumed", False))
        complete_event()
        raise RestartProcessLoop(resume_consumed=resume_consumed, dotfiles_updated=dotfiles_updated)

    def wait_for_changes(*_args: object, **_kwargs: object) -> bool:
        nonlocal waits
        waits += 1
        complete_event()
        return True

    def exit_abnormal(orchestrator: str, returncode: int, detail: str = "") -> None:
        abnormal_returncodes.append(returncode)
        complete_event()
        _EXIT_ABNORMAL_SESSION_IMPL(orchestrator, returncode, detail)

    def run_process_loop(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
        nonlocal marker_consumptions
        marker_consumptions += int(bool(args.internal_dotfiles_updated))
        _CMD_PROCESS_LOOP_IMPL(args, private_notes)

    monkeypatch.setattr(process_loop, "_consume_process_loop_abort", consume_abort)
    monkeypatch.setattr(process_loop, "_pull_private_notes", pull_notes)
    monkeypatch.setattr(process_loop, "_count_pending_entries", count_pending)
    monkeypatch.setattr(process_loop, "_update_before_session", update_before)
    monkeypatch.setattr(process_loop, "_prepare_session_target", prepare_target)
    monkeypatch.setattr(process_loop, "_build_session_argv", build_session_argv)
    monkeypatch.setattr(process_loop._process_loop_log, "consume_instructions", consume_instructions)
    monkeypatch.setattr(process_loop.subprocess, "run", run_child)
    monkeypatch.setattr(process_loop, "_restart_process_loop", restart_process_loop)
    monkeypatch.setattr(process_loop, "_wait_for_changes", wait_for_changes)
    monkeypatch.setattr(process_loop, "_exit_abnormal_session", exit_abnormal)
    monkeypatch.setattr(process_loop, "_cmd_process_loop", run_process_loop)

    cli_args = ["wi", "process-loop", f"--target-repo={repo}", "--no-alerts"]
    if resume_requested:
        cli_args.append("--resume=session")
    if update_marker:
        cli_args.append("--internal-dotfiles-updated")
    actual_exit = 0
    while True:
        try:
            agent_toolkit.atk.main(cli_args, home=case_root)
        except RestartProcessLoop as restart:
            if completed_events >= len(expected_trace):
                break
            cli_args = ["wi", "process-loop", f"--target-repo={repo}", "--no-alerts"]
            if resume_requested and not restart.resume_consumed:
                cli_args.append("--resume=session")
            else:
                resume_requested = False
            if restart.dotfiles_updated:
                cli_args.append("--internal-dotfiles-updated")
            continue
        except SystemExit as exc:
            assert isinstance(exc.code, int)
            actual_exit = exc.code
        break
    assert actual_exit == expected_exit
    assert completed_events == len(expected_trace)


def test_restart_consumes_resume_and_one_shot_markers_once(tmp_path: pathlib.Path) -> None:
    """既知事例: resumeと更新済み印は再起動引数で重複しない。"""
    script = tmp_path / "atk.py"
    script.write_text("", encoding="utf-8")
    _, args = process_loop._build_restart_target(
        [
            str(script),
            "--resume",
            "session",
            process_loop._INTERNAL_MISE_REFRESHED_ARG,
            process_loop._INTERNAL_DOTFILES_UPDATED_ARG,
        ],
        resume_consumed=True,
        mise_refreshed=True,
        dotfiles_updated=True,
    )
    assert "--resume" not in args
    assert "session" not in args
    assert args.count(process_loop._INTERNAL_MISE_REFRESHED_ARG) == 1
    assert args.count(process_loop._INTERNAL_DOTFILES_UPDATED_ARG) == 1
