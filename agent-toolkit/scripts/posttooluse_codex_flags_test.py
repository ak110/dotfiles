"""agent-toolkit/scripts/posttooluse.pyのAgent/Task起動セッション状態フラグ記録のテスト。

subagent_type別フラグ記録・codex-review起動検出・plan-codex-reviewer経由検査用フラグ
（plan_codex_reviewer_invoked / plan_codex_reviewer_blocked / recorded_codex_thread_id）を検証する。
`posttooluse_test.py`のpylint too-many-lines回避のため独立ファイルへ配置する。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "posttooluse.py"


def _run(payload: dict, *, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, input=json.dumps(payload, ensure_ascii=False), env=env)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TestAgentInvocationFlags:
    """AgentとTask起動のsubagent_type別セッション状態フラグ記録と、codex-review起動検出。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    @pytest.mark.parametrize(
        ("subagent_type", "flag_key"),
        [
            ("plan-reviewer", "plan_reviewer_invoked"),
            ("agent-toolkit:plan-reviewer", "plan_reviewer_invoked"),
            ("plan-impl-reviewer", "plan_impl_reviewer_invoked"),
            ("agent-toolkit:plan-impl-reviewer", "plan_impl_reviewer_invoked"),
            ("agent-doc-validator", "agent_doc_validator_invoked"),
            ("agent-toolkit:agent-doc-validator", "agent_doc_validator_invoked"),
            ("plan-codex-reviewer", "codex_review_invoked"),
            ("agent-toolkit:plan-codex-reviewer", "codex_review_invoked"),
        ],
    )
    def test_subagent_type_flag(self, tmp_path: pathlib.Path, tool_name: str, subagent_type: str, flag_key: str):
        sid = f"{tool_name.lower()}-{subagent_type.replace(':', '-')}"
        _run({"session_id": sid, "tool_name": tool_name, "tool_input": {"subagent_type": subagent_type}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get(flag_key) is True

    def test_codex_review_flag_via_mcp(self, tmp_path: pathlib.Path):
        sid = "codex-review-via-mcp"
        _run({"session_id": sid, "tool_name": "mcp__codex__codex", "tool_input": {}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_invoked") is True

    def test_codex_review_not_recorded_via_mcp_when_sidechain(self, tmp_path: pathlib.Path):
        """`isSidechain`が真（`plan-codex-implementer`内部呼び出し）の場合、`codex_review_invoked`を記録しない。

        `isSidechain`が偽の場合に記録される挙動（従来どおり）は`test_codex_review_flag_via_mcp`で検証済み。
        """
        sid = "codex-sidechain-mcp-no-review-flag"
        _run({"session_id": sid, "tool_name": "mcp__codex__codex", "tool_input": {}, "isSidechain": True}, state_dir=tmp_path)
        assert _read_state(tmp_path, sid).get("codex_review_invoked") is not True

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_other_subagent_type_no_flag(self, tmp_path: pathlib.Path, tool_name: str):
        sid = f"{tool_name.lower()}-other-subagent"
        _run({"session_id": sid, "tool_name": tool_name, "tool_input": {"subagent_type": "claude"}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("plan_reviewer_invoked") is not True
        assert state.get("plan_impl_reviewer_invoked") is not True
        assert state.get("agent_doc_validator_invoked") is not True
        assert state.get("codex_review_invoked") is not True

    def test_plan_codex_reviewer_subagent_sets_both_flags(self, tmp_path: pathlib.Path):
        """plan-codex-reviewer起動時はcodex_review_invokedとplan_codex_reviewer_invokedを両方真化する。"""
        sid = "fb4-both-flags"
        _run(
            {"session_id": sid, "tool_name": "Task", "tool_input": {"subagent_type": "agent-toolkit:plan-codex-reviewer"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_invoked") is True
        assert state.get("plan_codex_reviewer_invoked") is True

    def test_mcp_codex_direct_call_sets_only_codex_review_invoked(self, tmp_path: pathlib.Path):
        """mcp__codex__codex直接呼び出しはcodex_review_invokedのみ真化し、threadIdを記録する。"""
        sid = "fb4-direct-call"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__codex__codex",
                "tool_input": {},
                "isSidechain": False,
                "tool_response": {"threadId": "th_direct"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_invoked") is True
        assert state.get("plan_codex_reviewer_invoked") is not True
        assert state.get("recorded_codex_thread_id") == "th_direct"

    def test_plan_codex_reviewer_post_tool_use_failure_sets_blocked_flag(self, tmp_path: pathlib.Path):
        """PostToolUseFailure（実行時失敗）でplan_codex_reviewer_blockedを真化する。"""
        sid = "fb4-post-failure"
        _run(
            {
                "session_id": sid,
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Task",
                "tool_input": {"subagent_type": "agent-toolkit:plan-codex-reviewer"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_codex_reviewer_blocked") is True

    def test_plan_codex_reviewer_permission_denied_sets_blocked_flag(self, tmp_path: pathlib.Path):
        """PermissionDenied（auto mode下の権限拒否）でplan_codex_reviewer_blockedを真化する。"""
        sid = "fb4-perm-denied"
        _run(
            {
                "session_id": sid,
                "hook_event_name": "PermissionDenied",
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "plan-codex-reviewer"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_codex_reviewer_blocked") is True
