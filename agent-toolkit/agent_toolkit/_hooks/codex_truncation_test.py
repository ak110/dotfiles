"""Codexが省略したBashの出力へ、元の量と回収の操作を示す通知を公開hookで確かめる。"""

import json

import pytest

from agent_toolkit._hooks import codex_truncation
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_TRUNCATED = "Warning: truncated output (original token count: 9000)\n先頭\n…\n末尾\n"


@pytest.mark.parametrize(
    ("codex", "response", "notified"),
    [(True, _TRUNCATED, True), (True, "全文の出力\n", False), (False, {"stdout": _TRUNCATED, "stderr": ""}, False)],
)
def test_truncated_codex_output_gets_recovery_notice(tmp_path, codex: bool, response: object, notified: bool) -> None:
    """Codexの省略の包絡だけへ通知し、省略の無い出力とClaude Codeの出力には出さない。"""
    payload = {
        "session_id": "codex-truncation",
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_use_id": "call-1",
        "tool_name": "Bash",
        "tool_input": {"command": "cat large.log"},
        "tool_response": response,
        **({"turn_id": "turn"} if codex else {}),
    }
    result = _run_hook_in_process("posttooluse", payload)
    assert result.returncode == 0
    context = json.loads(result.stdout).get("hookSpecificOutput", {}).get("additionalContext", "") if result.stdout else ""
    assert ("観測した元の出力量は9000トークン。" in context) is notified
    assert (codex_truncation.TRUNCATED_OUTPUT_NOTICE in context) is notified
