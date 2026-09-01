"""計画作業rootの保存確認Stopフックの公開契約を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _write_transcript

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path,
    home: pathlib.Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env.update(
        {
            "TMPDIR": str(state_dir),
            "TEMP": str(state_dir),
            "TMP": str(state_dir),
            "HOME": str(home),
            "USERPROFILE": str(home),
        }
    )
    for name in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "DOTFILES_AUTONOMOUS_EXIT_REQUIRED",
    ):
        env.pop(name, None)
    env.update(extra_env or {})
    return _fork_runner.run_script(_SCRIPT, argv=("plan_save_advisor",), input=payload_text, env=env)


def _payload(session_id: str, transcript: pathlib.Path, **extra: object) -> dict:
    return {"session_id": session_id, "transcript_path": str(transcript), **extra}


def _decision(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0
    return json.loads(result.stdout)


def test_existing_working_plans_block_once_then_approve(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    first = plans / "first.md"
    second = plans / "second.md"
    first.write_text("# first\n", encoding="utf-8")
    second.write_text("# second\n", encoding="utf-8")
    transcript = _write_transcript(tmp_path, [])
    session_id = "block-once"
    _write_state(tmp_path, session_id, {"session_plan_main_paths": [str(second), str(first)]})

    first_result = _decision(_run(_payload(session_id, transcript), state_dir=tmp_path, home=home))
    second_result = _decision(_run(_payload(session_id, transcript), state_dir=tmp_path, home=home))

    assert first_result["decision"] == "block"
    assert str(first) in first_result["reason"]
    assert str(second) in first_result["reason"]
    assert "atk plans commit <relative main plan path>" in first_result["reason"]
    assert not second_result


@pytest.mark.parametrize(
    ("state", "payload_extra", "environment"),
    [
        ({"working_plan_save_notified": True}, {}, {}),
        ({}, {"stop_hook_active": True}, {}),
        ({}, {"background_tasks": [{"type": "subagent", "id": "pending"}]}, {}),
        ({}, {}, {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}),
        ({}, {}, {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}),
        ({}, {}, {"DOTFILES_AUTONOMOUS_EXIT_REQUIRED": "1"}),
    ],
)
def test_suppression_conditions_approve(
    tmp_path: pathlib.Path,
    state: dict,
    payload_extra: dict,
    environment: dict[str, str],
) -> None:
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    plan = plans / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    transcript = _write_transcript(tmp_path, [])
    session_id = "suppressed"
    _write_state(tmp_path, session_id, {"session_plan_main_paths": [str(plan)], **state})

    result = _run(
        _payload(session_id, transcript, **payload_extra),
        state_dir=tmp_path,
        home=home,
        extra_env=environment,
    )

    assert not _decision(result)


def test_missing_and_private_notes_paths_approve(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    private_plan = tmp_path / "private-notes" / "plans" / "2026" / "09" / "01-saved-a1b2.md"
    private_plan.parent.mkdir(parents=True)
    private_plan.write_text("# saved\n", encoding="utf-8")
    transcript = _write_transcript(tmp_path, [])
    session_id = "outside-working-root"
    _write_state(
        tmp_path,
        session_id,
        {"session_plan_main_paths": [str(private_plan), str(home / ".claude" / "plans" / "missing.md")]},
    )

    result = _run(_payload(session_id, transcript), state_dir=tmp_path, home=home)

    assert not _decision(result)


@pytest.mark.parametrize("payload", ["not json", {}, {"session_id": ""}])
def test_invalid_payload_approves(tmp_path: pathlib.Path, payload: object) -> None:
    result = _run(payload, state_dir=tmp_path, home=tmp_path / "home")
    assert not _decision(result)
