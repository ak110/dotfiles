"""巡回ランチャーの公開CLIから再起動と中断を検証する。"""

from __future__ import annotations

import pathlib
import signal

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop, process_loop_control, process_loop_env, process_loop_log, process_loop_session


def _launch(monkeypatch: pytest.MonkeyPatch, outcomes: list[bool], tmp_path: pathlib.Path) -> list[pathlib.Path]:
    started: list[pathlib.Path] = []
    results = iter(outcomes)
    monkeypatch.setattr(process_loop_control, "read_resume_request", lambda: None)
    monkeypatch.setattr(process_loop_control, "consume_process_loop_abort", lambda: False)
    monkeypatch.setattr(process_loop_session, "resolve_orchestrator_specs", lambda: [("claude", "test", "medium")])
    monkeypatch.setattr(
        process_loop_session,
        "select_available_orchestrator",
        lambda *_args, **_kwargs: ("claude", "test", "medium", None, None),
    )
    monkeypatch.setattr(process_loop_log, "log_path", lambda: tmp_path / "launcher.log")
    monkeypatch.setattr(process_loop.time, "sleep", lambda _: None)

    def run(_args, cwd, prompt, env, **_kwargs):
        assert "process-loop" in prompt
        assert env.get(process_loop_env.PROCESS_LOOP_SESSION_ENV) == "1"
        started.append(cwd)
        return next(results)

    monkeypatch.setattr(process_loop_session, "run_process_session", run)
    return started


@pytest.mark.parametrize(
    ("arguments", "outcomes", "status"),
    [
        (["--once"], [True], 0),
        (["--once"], [False], 1),
        ([], [False, False, True, False, False, False], 1),
    ],
)
def test_public_launcher_runs_in_cwd_and_retries_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, arguments: list[str], outcomes: list[bool], status: int
) -> None:
    started = _launch(monkeypatch, outcomes, tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(process_loop_env.PROCESS_LOOP_SESSION_ENV, raising=False)

    with pytest.raises(SystemExit) as exit_result:
        atk.main(["wi", "process-loop", *arguments], home=tmp_path)

    assert exit_result.value.code == status
    assert started == [tmp_path] * len(outcomes)


def test_public_launcher_does_not_restart_after_abort(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    started = _launch(monkeypatch, [True], tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(process_loop_control, "consume_process_loop_abort", lambda: bool(started))

    with pytest.raises(SystemExit) as exit_result:
        atk.main(["wi", "process-loop"], home=tmp_path)

    assert exit_result.value.code == 0
    assert started == [tmp_path]


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_public_launcher_does_not_restart_after_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, signum: int
) -> None:
    _launch(monkeypatch, [], tmp_path)
    signals = []

    def stop(*_args, **_kwargs):
        signals.append(signum)
        process_loop._stop(signum, None)  # pylint: disable=protected-access

    monkeypatch.setattr(process_loop_session, "run_process_session", stop)
    with pytest.raises(SystemExit) as exit_result:
        atk.main(["wi", "process-loop"], home=tmp_path)

    assert exit_result.value.code == 128 + signum
    assert signals == [signum]
