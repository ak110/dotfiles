"""Code Modeでも設定PreToolUseがメイン専用ツールの利用主体を制限する。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.pretooluse_support import _plan_file_state_env, run_without_skill_notices


@pytest.mark.parametrize("tool", ["send_to_user", "compact_conversation", "apply_runtime_changes"])
@pytest.mark.parametrize("actor", ["main", "subagent", "delegate", "owner"])
@pytest.mark.parametrize("prefix", ["mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__"])
def test_main_only_mcp_tools_enforce_actor(tmp_path: pathlib.Path, tool: str, actor: str, prefix: str) -> None:
    env = _plan_file_state_env(tmp_path)
    env["AGENT_TOOLKIT_DELEGATED_SESSION"] = "1" if actor == "delegate" else ""
    env["AGENT_TOOLKIT_OWNER_SESSION"] = "parent" if actor == "owner" else ""
    result = run_without_skill_notices(
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


@pytest.mark.parametrize(
    ("message", "decision"),
    [
        ("## 発話の理解\n- **指摘（原文）**: atk agents-exit-session はどうした？", "deny"),
        ("**発話の理解**\n- **要求（原文）:** 上の2件について再発防止まで実施する。", "deny"),
        ("## 発話の理解\n- 指摘: セッション終了手順が未実行ではないか。", "allow"),
        ("## 発話の理解\n- 指摘: 終了手順が未実行ではないか。\n## 記録\n- 原文: ん？ atk agent-exit-sessionは？？", "allow"),
        ("## 発話の理解\n終了手順の確認。\n\n```markdown\n- 原文: 引用の例\n```", "allow"),
        ("**発話の理解**\n終了手順の確認。\n\n**記録**\n- 原文: ん？ atk agent-exit-sessionは？？", "allow"),
        ("発話の理解：指摘（原文）：終了手順が未実行ではないか。", "deny"),
        ("**発話の理解：**\n- **指摘（原文）：** 終了手順が未実行ではないか。", "deny"),
        ("**発話の理解：** **原文：** 終了手順を確認する。", "deny"),
        ("## 発話の理解\n終了手順の確認。\n\n**記録：**\n- 原文: ん？ atk agent-exit-sessionは？？", "allow"),
    ],
)
def test_send_to_user_utterance_understanding(tmp_path: pathlib.Path, message: str, decision: str) -> None:
    result = run_without_skill_notices(
        {
            "tool_name": "mcp__agents_server__send_to_user",
            "tool_input": {"message": message},
            "session_id": "utterance-understanding",
            "turn_id": "turn-1",
            "cwd": str(tmp_path),
        },
        env_overrides=_plan_file_state_env(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout or "{}").get("hookSpecificOutput", {})
    assert output.get("permissionDecision", "allow") == decision
    if decision == "deny":
        assert "「発話の理解」に「原文」" in output["permissionDecisionReason"]
