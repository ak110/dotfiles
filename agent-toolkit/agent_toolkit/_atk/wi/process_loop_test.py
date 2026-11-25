from __future__ import annotations

import pathlib
import signal

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop, process_loop_control, process_loop_env, process_loop_log, process_loop_session
from agent_toolkit._common import process_tree


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


def test_public_launcher_reports_unreclaimed_child_without_retry(monkeypatch, tmp_path, capsys) -> None:
    started = _launch(monkeypatch, [], tmp_path)

    def incomplete(_args, cwd, *_rest, **_kwargs):
        started.append(cwd)
        raise process_tree.ReclaimError(["4321: child"], [], context="process-loop usage-limit switch")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(process_loop_session, "run_process_session", incomplete)
    with pytest.raises(SystemExit) as exit_result:
        atk.main(["wi", "process-loop"], home=tmp_path)
    assert exit_result.value.code == 1
    assert started == [tmp_path]
    stderr = capsys.readouterr().err
    assert "失敗:" in stderr and "4321: child" in stderr and "次の操作:" in stderr
    assert "Traceback" not in stderr


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


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([], ("0", "1800")),
        (["--no-alerts", "--alert-interval", "45"], ("1", "45")),
    ],
)
def test_public_launcher_passes_alert_settings_to_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, flags: list[str], expected: tuple[str, str]
) -> None:
    _launch(monkeypatch, [], tmp_path)
    captured: list[tuple[str, str]] = []

    def run(_args, _cwd, _prompt, env, **_kwargs):
        captured.append((env[process_loop_env.NO_ALERTS_ENV], env[process_loop_env.ALERT_INTERVAL_ENV]))
        return True

    monkeypatch.setattr(process_loop_session, "run_process_session", run)
    with pytest.raises(SystemExit) as result:
        atk.main(["wi", "process-loop", "--once", *flags], home=tmp_path)
    assert result.value.code == 0
    assert captured == [expected]
