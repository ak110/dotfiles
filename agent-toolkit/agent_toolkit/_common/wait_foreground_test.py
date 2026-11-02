"""背景機能とTTLに対応するCLI専用期限。"""

import pytest

from agent_toolkit._common import wait_schedule


@pytest.mark.parametrize(
    ("claude", "disabled", "ttl", "delegated", "cli", "other"),
    [
        (True, "1", "1h", False, 540.0, 1740.0),
        (True, "1", "5m", False, 270.0, 270.0),
        (True, "0", "1h", False, 1740.0, 1740.0),
        (True, "1", "1h", True, 540.0, 240.0),
        (False, "1", "1h", False, 270.0, 270.0),
    ],
)
def test_cli_foreground_cap_preserves_mcp_and_lane_limits(monkeypatch, claude, disabled, ttl, delegated, cli, other):
    for key in ("CLAUDECODE", "AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "FORCE_PROMPT_CACHING_5M"):
        monkeypatch.delenv(key, raising=False)
    if claude:
        monkeypatch.setenv("CLAUDECODE", "1")
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", disabled)
    monkeypatch.setenv("CLAUDE_CODE_PROMPT_CACHE_TTL", ttl)
    assert wait_schedule.get_cli_wait_timeout("main") == cli
    assert wait_schedule.get_wait_timeout("main") == other
