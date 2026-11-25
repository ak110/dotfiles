"""送達した報告への警告を、PostToolUseのhookが同じ呼出しの添付として届けることを確かめる。 pyfltr: allow-comment"""

import json
import pathlib
import tempfile

import pytest

from agent_toolkit._common import sent_warnings
from agent_toolkit._common.session_state import state_path
from agent_toolkit._hooks.user_prompt_submit_test import _run_subcommand

_SEND_TO_USER = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
_WARN = '<atk-auto source="send-to-user" kind="warn">\n本文は表示済みである。\n</atk-auto>'


@pytest.fixture(autouse=True)
def _redirect_tempdir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    state_path("relay").parent.mkdir(parents=True, exist_ok=True)


def test_posttooluse_delivers_stashed_warning_outside_tool_result(tmp_path: pathlib.Path) -> None:
    """PostToolUseのhookが、預けた警告を同じ呼出しの添付（additionalContext）として届ける。 pyfltr: allow-comment"""
    sent_warnings.stash("relay", "toolu_send", [_WARN])
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "relay",
        "tool_name": _SEND_TO_USER,
        "tool_use_id": "toolu_send",
        "tool_input": {"message": "報告"},
        "tool_response": {"status": "confirmed"},
    }
    result = _run_subcommand("posttooluse", payload, state_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "本文は表示済みである。" in context
    again = _run_subcommand("posttooluse", payload, state_dir=tmp_path)
    assert "本文は表示済みである。" not in again.stdout
