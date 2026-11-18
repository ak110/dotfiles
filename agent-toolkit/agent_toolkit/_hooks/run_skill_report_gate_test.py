"""run-skillのセッションの報告保存Stopフックの判定を検証する。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._common import mcp_environment
from agent_toolkit._hooks import run_skill_report_gate
from agent_toolkit._testing.helpers import _write_transcript

_RUN_ID = "run-1"
_SKILL = "my-skill"


def _assistant_text(text: str = "作業を終えた。") -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"},
    }


def _payload(tmp_path: pathlib.Path, **extra: Any) -> str:
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "x"}}, _assistant_text()])
    return json.dumps({"session_id": "run-skill-session", "transcript_path": str(transcript), **extra}, ensure_ascii=False)


@pytest.fixture(name="calls")
def _calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """run-skillの起動環境を設定し、保存確認の呼び出しを記録する。"""
    monkeypatch.setenv(run_skill_report_gate.RUN_ID_ENV, _RUN_ID)
    monkeypatch.setenv(mcp_environment.SESSION_START_INPUT_ENV, json.dumps({"source": "run-skill", "skill": _SKILL}))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(wi_add, "has_synced_run_report", _recorder(recorded, saved=False))
    return recorded


def _recorder(recorded: list[dict[str, Any]], *, saved: bool):
    def has_synced_run_report(private_notes: pathlib.Path, **kwargs: Any) -> bool:
        recorded.append({"private_notes": private_notes, **kwargs})
        return saved

    return has_synced_run_report


def test_blocks_until_report_is_saved(tmp_path: pathlib.Path, calls: list[dict[str, Any]]) -> None:
    decision, reason = run_skill_report_gate.evaluate(_payload(tmp_path))

    assert decision == "block"
    assert 'source="run_skill_report_gate"' in reason
    assert [(call["run_id"], call["skill"]) for call in calls] == [(_RUN_ID, _SKILL)]


def test_approves_after_report_is_saved(
    tmp_path: pathlib.Path, calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wi_add, "has_synced_run_report", _recorder(calls, saved=True))

    assert run_skill_report_gate.evaluate(_payload(tmp_path)) == ("approve", "")
    assert len(calls) == 1


def test_check_failure_is_treated_as_unsaved(
    tmp_path: pathlib.Path, calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    del calls

    def failing(_private_notes: pathlib.Path, **_kwargs: Any) -> bool:
        raise OSError("git failed")

    monkeypatch.setattr(wi_add, "has_synced_run_report", failing)

    assert run_skill_report_gate.evaluate(_payload(tmp_path))[0] == "block"


@pytest.mark.parametrize(
    ("env", "extra"),
    [
        ({run_skill_report_gate.RUN_ID_ENV: ""}, {}),
        ({mcp_environment.SESSION_START_INPUT_ENV: "{}"}, {}),
        ({"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, {}),
        ({}, {"agent_id": "subagent-1"}),
        ({}, {"background_tasks": [{"id": "b1", "type": "shell", "status": "running", "description": "build"}]}),
    ],
    ids=["no-run-id", "no-skill", "delegated", "subagent", "background-task"],
)
def test_approves_without_checking_outside_target(
    tmp_path: pathlib.Path,
    calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    extra: dict[str, Any],
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert run_skill_report_gate.evaluate(_payload(tmp_path, **extra)) == ("approve", "")
    assert not calls
