"""ホストの背景機能と期限、出力接続警告の回復手段を組み合わせる。"""

import pytest

from agent_toolkit._common import wait_schedule
from agent_toolkit._testing.pretooluse_support import _additional_context, _run


@pytest.mark.parametrize(("claude", "disabled", "limit"), [(True, "1", 540.0), (True, "0", 1740.0), (False, "0", 270.0)])
def test_wait_warning_uses_available_recovery(monkeypatch, claude, disabled, limit):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("FORCE_PROMPT_CACHING_5M", raising=False)
    if claude:
        monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", disabled)
    monkeypatch.setenv("CLAUDE_CODE_PROMPT_CACHE_TTL", "1h")
    result = _run({"tool_name": "Bash", "tool_input": {"command": "atk agents wait >/dev/null &"}})
    assert result.returncode == 0 and not result.stderr
    notice = _additional_context(result)
    assert ("run_in_background" in notice) is (claude and disabled != "1")
    assert wait_schedule.get_cli_wait_timeout("main") == limit
    if disabled == "1":
        assert "timeout=600000" in notice and "同じターン内" in notice
    corrected = _run({"tool_name": "Bash", "tool_input": {"command": "atk agents wait", "timeout": 600000}})
    assert corrected.returncode == 0 and not corrected.stderr
    assert "結果と終了状態を直接受領できない入力" not in _additional_context(corrected)
