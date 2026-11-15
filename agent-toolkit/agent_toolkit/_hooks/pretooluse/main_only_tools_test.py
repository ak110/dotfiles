"""Code Modeでも設定PreToolUseがメイン専用ツールの利用主体を制限する。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, run_without_read_delivery


@pytest.mark.parametrize("tool", ["send_to_user", "compact_conversation", "apply_runtime_changes"])
@pytest.mark.parametrize("actor", ["main", "subagent", "delegate", "owner"])
@pytest.mark.parametrize("prefix", ["mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__"])
def test_main_only_mcp_tools_enforce_actor(tmp_path: pathlib.Path, tool: str, actor: str, prefix: str) -> None:
    env = _plan_file_state_env(tmp_path)
    env["AGENT_TOOLKIT_DELEGATED_SESSION"] = "1" if actor == "delegate" else ""
    env["AGENT_TOOLKIT_OWNER_SESSION"] = "parent" if actor == "owner" else ""
    result = run_without_read_delivery(
        {
            "tool_name": f"{prefix}{tool}",
            "tool_input": {"message": "処理結果を報告する。"} if tool == "send_to_user" else {},
            "session_id": "main-only-tools",
            "cwd": str(tmp_path),
            **({"agent_id": "nested-agent"} if actor == "subagent" else {}),
        },
        env_overrides=env,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == ("allow" if actor == "main" else "deny")
