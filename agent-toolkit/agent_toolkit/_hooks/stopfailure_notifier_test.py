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
    [("rate_limit", False, None), ("server_error", False, None), ("rate_limit", True, None), ("rate_limit", False, "child")],
)
def test_rate_limit_command_hook_hands_request_to_turn_complete(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, error: str, delegated: bool, agent: str | None
) -> None:
    """StopFailureの公開errorからメインだけの再開要求を作成する。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert (
        stopfailure_notifier.main(
            json.dumps({"hook_event_name": "StopFailure", "session_id": "limit-test", "error": error, "agent_id": agent})
        )
        == 0
    )
    request = tmp_path / "claude/agent-toolkit-function-hooks/rate-limit-limit-test.txt"
    assert request.exists() is (error == "rate_limit" and not delegated and agent is None)
    if request.exists():
        assert request.read_text(encoding="utf-8") == "requested"


def test_failure_does_not_create_a_log_or_transcript_summary(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        "error": "server_error",
        "transcript_path": str(transcript),
    }
    assert stopfailure_notifier.main(json.dumps(payload)) == 0
    assert not list(temp.iterdir())
    assert not (tmp_path / "claude").exists()


def test_invalid_json_exits_safely():
    """不正JSON入力でも例外を送出せず正常終了する。"""
    result = _fork_runner.run_script(_SCRIPT, argv=("stopfailure_notifier",), input="not json")
    assert result.returncode == 0
