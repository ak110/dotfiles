"""ホストの背景機能と期限、出力接続警告とSessionStartの待機案内を同じ組合せで確かめる。"""

import pytest

from agent_toolkit._common import wait_schedule
from agent_toolkit._hooks import rules_context
from agent_toolkit._testing.pretooluse_support import _additional_context, _run


@pytest.mark.parametrize(
    ("claude", "disabled", "limit"), [(True, "1", 540.0), (True, "0", 1740.0), (False, "0", 270.0), (False, "1", 270.0)]
)
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
    # 起動設定を継承する委譲先にもメインと同じ案内を届け、案内した前景上限の内側でCLIが先に返ることを確かめる。
    for delegated in (False, True):
        context = rules_context.compose_session_start("startup", delegated=delegated, host="claude" if claude else "codex")
        announced = rules_context.BACKGROUND_TASKS_DISABLED_NOTICE in (context or "")
        assert announced is (claude and disabled == "1")
        if announced:
            assert "timeout=600000" in rules_context.BACKGROUND_TASKS_DISABLED_NOTICE
            assert limit * 1000 < 600000
    corrected = _run({"tool_name": "Bash", "tool_input": {"command": "atk agents wait", "timeout": 600000}})
    assert corrected.returncode == 0 and not corrected.stderr
    assert "結果と終了状態を直接受領できない入力" not in _additional_context(corrected)
