"""agent-toolkit/agent_toolkit/_hooks/stopfailure_notifier.py のテスト。

利用上限の再開要求の作成条件と、それ以外の失敗で保存物を作成しないことを検証する。
"""

import json
import pathlib
import tempfile

import pytest

from agent_toolkit._hooks import stopfailure_notifier
from agent_toolkit._testing import fork_runner as _fork_runner

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"


@pytest.mark.parametrize(
    "error,delegated,agent",
    [
        (error, delegated, agent)
        for error in ("rate_limit", "server_error")
        for delegated, agent in ((False, None), (True, None), (False, "child"))
    ],
)
def test_retryable_failure_command_hook_hands_request_to_turn_complete(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, error: str, delegated: bool, agent: str | None
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert (
        stopfailure_notifier.main(
            json.dumps(
                {
                    "hook_event_name": "StopFailure",
                    "session_id": "limit-test",
                    "prompt_id": "first-prompt",
                    "error": error,
                    "agent_id": agent,
                }
            )
        )
        == 0
    )
    directory = tmp_path / "claude/agent-toolkit-function-hooks"
    expected = [f"{error.replace('_', '-')}-limit-test.first-prompt.txt"] if not delegated and agent is None else []
    assert sorted(path.name for path in directory.glob("*.txt")) == expected
    for name in expected:
        assert (directory / name).read_text(encoding="utf-8") == "requested"


@pytest.mark.parametrize("prompt_id", [None, "", "../escape", []])
def test_unidentified_interactive_failure_has_no_resume_request(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, prompt_id: object
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", raising=False)
    payload = {"session_id": "s1", "prompt_id": prompt_id, "error": "server_error"}
    assert stopfailure_notifier.main(json.dumps(payload)) == 0
    assert not (tmp_path / "claude").exists()


def test_process_loop_keeps_session_scoped_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "s1")
    payload = {"session_id": "s1", "prompt_id": "p1", "error": "rate_limit"}
    assert stopfailure_notifier.main(json.dumps(payload)) == 0
    request = tmp_path / "claude/agent-toolkit-function-hooks/rate-limit-s1.txt"
    assert request.read_text(encoding="utf-8") == "requested"


@pytest.mark.parametrize(
    "error",
    [
        "authentication_failed",
        "oauth_org_not_allowed",
        "account_on_hold",
        "billing_error",
        "invalid_request",
        "model_not_found",
        "max_output_tokens",
        "cloud_credential_error",
        "unknown",
    ],
)
def test_failure_does_not_create_a_log_or_transcript_summary(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    """再開要求の対象外の失敗では、一時領域と設定領域へ何も書かない。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": "StopFailure",
        "session_id": "s1",
        "error": error,
        "transcript_path": str(transcript),
    }
    assert stopfailure_notifier.main(json.dumps(payload)) == 0
    assert not list(temp.iterdir())
    assert not (tmp_path / "claude").exists()


def test_invalid_json_exits_safely():
    result = _fork_runner.run_script(_SCRIPT, argv=("stopfailure_notifier",), input="not json")
    assert result.returncode == 0
