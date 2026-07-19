"""agent-toolkit/scripts/posttooluse.pyのAgent/Task起動セッション状態フラグ記録のテスト。

subagent_type別フラグ記録・codex-review起動検出（mcp__codex__codex / mcp__codex__codex-reply）・
plan-codex-reviewer経由検査用フラグ
（plan_codex_reviewer_invoked / plan_codex_reviewer_blocked / recorded_codex_thread_id）を検証する。
`posttooluse_test.py`のpylint too-many-lines回避のため独立ファイルへ配置する。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest
from _scope_escalation_test_helpers import load_scope_escalation_inputs

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

    def test_plan_codex_reviewer_subagent_sets_codex_review_invoked_only(self, tmp_path: pathlib.Path):
        """plan-codex-reviewer起動時はcodex_review_invokedのみ真化する（FB[2]反映後、`plan_codex_reviewer_invoked`はPreToolUse側の責務）。"""
        sid = "fb2-post-codex-review-only"
        _run(
            {"session_id": sid, "tool_name": "Task", "tool_input": {"subagent_type": "agent-toolkit:plan-codex-reviewer"}},
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_invoked") is True
        assert state.get("plan_codex_reviewer_invoked") is not True

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

    def test_mcp_codex_reply_sets_codex_review_invoked(self, tmp_path: pathlib.Path):
        """mcp__codex__codex-reply（継続呼び出し）成功時にcodex_review_invokedが真化する。"""
        sid = "fb-000318-001-codex-reply"
        _run({"session_id": sid, "tool_name": "mcp__codex__codex-reply", "tool_input": {}}, state_dir=tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("codex_review_invoked") is True

    def test_mcp_codex_reply_records_recorded_thread_id(self, tmp_path: pathlib.Path):
        """mcp__codex__codex-reply成功時のtool_response.threadIdがrecorded_codex_thread_idへ記録される。"""
        sid = "fb-000318-001-codex-reply-thread"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__codex__codex-reply",
                "tool_input": {},
                "isSidechain": False,
                "tool_response": {"threadId": "th_reply"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("recorded_codex_thread_id") == "th_reply"

    def test_mcp_codex_reply_not_recorded_when_sidechain(self, tmp_path: pathlib.Path):
        """`isSidechain`が真のmcp__codex__codex-reply呼び出しではcodex_review_invokedを記録しない。"""
        sid = "fb-000318-001-codex-reply-sidechain"
        _run(
            {"session_id": sid, "tool_name": "mcp__codex__codex-reply", "tool_input": {}, "isSidechain": True},
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("codex_review_invoked") is not True

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


class TestPlanImplExecutorActiveSessions:
    """`plan-impl-executor`系Agent/Task起動時のサブセッションID辞書記録。"""

    @pytest.mark.parametrize("subagent_type", ["plan-impl-executor", "agent-toolkit:plan-impl-executor"])
    def test_plan_impl_executor_registers_active_session(self, tmp_path: pathlib.Path, subagent_type: str):
        sid = f"fb6-active-{subagent_type.replace(':', '-')}"
        _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": subagent_type},
                "tool_response": {"agentId": "sub-session-123"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        active = state.get("plan_impl_executor_active_subagent_sessions")
        assert isinstance(active, dict)
        assert "sub-session-123" in active
        assert active["sub-session-123"]["subagent_type"] == subagent_type
        assert isinstance(active["sub-session-123"].get("started_at"), (int, float))

    def test_non_plan_impl_executor_does_not_register(self, tmp_path: pathlib.Path):
        """`spec-driven-implementer`等の他エージェント起動時はフラグへ書き込まない。"""
        sid = "fb6-non-plan-impl-executor"
        _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "spec-driven-implementer"},
                "tool_response": {"agentId": "sub-session-999"},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_impl_executor_active_subagent_sessions") in (None, {})

    def test_missing_agent_id_does_not_register(self, tmp_path: pathlib.Path):
        """`tool_response.agentId`が欠落する場合は書き込みをスキップする。"""
        sid = "fb6-missing-agent-id"
        _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "plan-impl-executor"},
                "tool_response": {},
            },
            state_dir=tmp_path,
        )
        state = _read_state(tmp_path, sid)
        assert state.get("plan_impl_executor_active_subagent_sessions") in (None, {})


def _async_wait_phrase() -> str:
    """隔離フィクスチャからasync-waitカテゴリの検出語を1件取得する（検出語の直接転記を避ける）。"""
    inputs = load_scope_escalation_inputs()
    for text, category in inputs:
        if category == "async-wait":
            return text
    raise AssertionError("async-waitカテゴリの入力がフィクスチャに存在しない")


class TestAgentCompletionAsyncWaitDetection:
    """AgentとTaskのforeground完了報告本文に対するasync-wait検出 (FB-C)。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_content_text_with_async_wait_blocks(self, tmp_path: pathlib.Path, tool_name: str):
        """`content`配列内`text`欄の完了報告本文にasync-wait表現を含み、

        かつ`totalToolUseCount`が閾値以上の場合に`decision: block`が出力される。
        """
        sid = f"fbc-content-async-wait-{tool_name.lower()}"
        result = _run(
            {
                "session_id": sid,
                "tool_name": tool_name,
                "tool_input": {"subagent_type": "claude"},
                "tool_response": {
                    "content": [{"type": "text", "text": _async_wait_phrase()}],
                    "totalToolUseCount": 5,
                },
            },
            state_dir=tmp_path,
        )
        output = json.loads(result.stdout)
        assert output.get("decision") == "block"

    def test_result_string_with_async_wait_blocks(self, tmp_path: pathlib.Path):
        """`result`欄（文字列）の完了報告本文にasync-wait表現を含む場合も`decision: block`が出力される。"""
        sid = "fbc-result-async-wait"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude"},
                "tool_response": {"result": _async_wait_phrase(), "totalToolUseCount": 3},
            },
            state_dir=tmp_path,
        )
        output = json.loads(result.stdout)
        assert output.get("decision") == "block"

    def test_below_threshold_tool_use_count_does_not_block(self, tmp_path: pathlib.Path):
        """`totalToolUseCount`が閾値未満の場合はasync-wait表現を含んでいても`decision: block`を出力しない（fail-open）。"""
        sid = "fbc-below-threshold"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude"},
                "tool_response": {
                    "content": [{"type": "text", "text": _async_wait_phrase()}],
                    "totalToolUseCount": 2,
                },
            },
            state_dir=tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_missing_completion_text_does_not_block(self, tmp_path: pathlib.Path):
        """完了報告本文が抽出できない場合（候補キーがいずれも存在しない）は無処理で終了する。"""
        sid = "fbc-no-completion-text"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude"},
                "tool_response": {"totalToolUseCount": 10},
            },
            state_dir=tmp_path,
        )
        assert result.stdout.strip() == ""

    def test_normal_completion_text_does_not_block(self, tmp_path: pathlib.Path):
        """async-wait表現を含まない通常の完了報告本文は`decision: block`を出力しない。"""
        sid = "fbc-normal-completion"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "claude"},
                "tool_response": {
                    "content": [{"type": "text", "text": "実装・検証・コミットを完了した。"}],
                    "totalToolUseCount": 10,
                },
            },
            state_dir=tmp_path,
        )
        assert result.stdout.strip() == ""
