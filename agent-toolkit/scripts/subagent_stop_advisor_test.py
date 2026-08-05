"""subagent_stop_advisorのテスト。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import _fork_runner
import pytest
from _stop_gate_test import (
    _user_async_launched_entry,
    _user_background_bash_entry,
    _user_task_notification_entry,
    _write_transcript,
)

_SCRIPT = Path(__file__).parent / "claude_hook.py"


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    return _fork_runner.run_script(_SCRIPT, argv=("subagent_stop_advisor",), input=json.dumps(payload))


def _write_hook_transcripts(tmp_path: Path, parent_entries: list[dict], agent_entries: list[dict]) -> tuple[str, str]:
    """公式Hook入力と同じ親セッション記録と対象サブエージェント記録を生成する。"""
    parent_dir = tmp_path / "parent"
    agent_dir = tmp_path / "agent"
    parent_dir.mkdir()
    agent_dir.mkdir()
    parent = _write_transcript(parent_dir, parent_entries)
    agent = _write_transcript(agent_dir, [*_implementation_route_entries(), *agent_entries])
    return str(parent), str(agent)


def _implementation_route_entries() -> list[dict]:
    """Codex・Claude実装系のtool use/result fixtureを返す。"""
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_impl",
                        "name": "mcp__codex__codex",
                        "input": {"prompt": "execution_track: implementation"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_impl_agent",
                        "name": "Agent",
                        "input": {"prompt": "execution_track: implementation"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_failure_1",
                        "name": "Bash",
                        "input": {"operation_key": "tests", "command": "pytest"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_failure_2",
                        "name": "Bash",
                        "input": {"operation_key": "tests", "command": "pytest"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_operation",
                        "name": "Tool",
                        "input": {"operation_key": "operation", "value": "input"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_compare",
                        "name": "Tool",
                        "input": {
                            "operation_key": "expand-targets",
                            "previous_paths": [],
                            "current_added_paths": ["path-0", "path-1", "path-2", "path-3", "path-4"],
                        },
                    },
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_impl",
                        "content": json.dumps({"threadId": "th_impl"}),
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_impl_agent",
                        "content": "agentId: agent-implementation",
                    },
                    {"type": "tool_result", "tool_use_id": "toolu_failure_1", "content": "failed"},
                    {"type": "tool_result", "tool_use_id": "toolu_failure_2", "content": "failed"},
                    {"type": "tool_result", "tool_use_id": "toolu_operation", "content": "result"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_compare",
                        "content": json.dumps({"deduplicated_paths": ["path-0", "path-1", "path-2", "path-3", "path-4"]}),
                    },
                ]
            },
        },
    ]


def test_approves_empty_report_when_descendant_agent_is_pending(tmp_path: Path) -> None:
    """孫エージェントの起動が未消化の場合は実質空の完了報告でも承認する。"""
    parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry("toolu_descendant_pending")])
    result = _run({"last_assistant_message": "", "transcript_path": parent, "agent_transcript_path": agent})
    assert result.stdout == ""
    assert result.returncode == 0


def test_legacy_transcript_path_detects_pending_descendant_agent(tmp_path: Path) -> None:
    """`agent_transcript_path`が無い旧版入力では`transcript_path`を互換経路として使う。"""
    transcript = str(_write_transcript(tmp_path, [_user_async_launched_entry("toolu_legacy_descendant")]))
    result = _run({"last_assistant_message": "", "transcript_path": transcript})
    assert result.stdout == ""
    assert result.returncode == 0


def test_blocks_empty_report_when_only_background_bash_is_pending(tmp_path: Path) -> None:
    """background Bashジョブのみが未消化の場合は実質空の完了報告をブロックする。"""
    parent, agent = _write_hook_transcripts(tmp_path, [], [_user_background_bash_entry("toolu_bash_pending")])
    result = _run({"last_assistant_message": "", "transcript_path": parent, "agent_transcript_path": agent})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "empty" in body["reason"]


def test_blocks_incomplete_plan_report_when_only_background_bash_is_pending(tmp_path: Path) -> None:
    """background Bashジョブのみが未消化の場合は必須ラベルを欠く完了報告をブロックする。"""
    session_id = "sid-bash-format"
    agent_id = "sub-bash-format"
    _write_flag_state(tmp_path, session_id, agent_id)
    parent, agent = _write_hook_transcripts(
        tmp_path,
        [],
        [_user_background_bash_entry("toolu_bash_format")],
    )
    result = _run_with_state_dir(
        {
            "session_id": session_id,
            "agent_id": agent_id,
            "last_assistant_message": "status: completed\nsummary: 待機中",
            "transcript_path": parent,
            "agent_transcript_path": agent,
        },
        tmp_path,
    )
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "missing required labels" in body["reason"]


def test_stop_hook_active_bypasses_check() -> None:
    """`stop_hook_active`真は再blockせずapproveを返す。"""
    result = _run({"last_assistant_message": "再試行の報告", "stop_hook_active": True})
    body = json.loads(result.stdout)
    assert body.get("decision") == "approve"


def test_empty_message_blocks_as_empty_result() -> None:
    """空文字列の完了報告は`is_empty_completion_report`でblockする。"""
    result = _run({"last_assistant_message": ""})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_whitespace_only_message_blocks_as_empty_result() -> None:
    """trim後空の完了報告は`is_empty_completion_report`でblockする。"""
    result = _run({"last_assistant_message": "   \n  \t  "})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "empty" in body["reason"]


def test_skill_invocation_only_blocks_as_empty_result() -> None:
    """`Skill`呼び出し単独の完了報告はblockする。"""
    result = _run({"last_assistant_message": "Skill(skill='foo')"})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "Skill" in body["reason"]


def test_skill_invocation_with_body_passes() -> None:
    """`Skill`呼び出し後に完了本文が続く正常報告はblockされない。"""
    text = "Skill(skill='foo')\n\n点検実施済。指摘なし。次工程へ移行する。"
    result = _run({"last_assistant_message": text})
    assert result.stdout == ""
    assert result.returncode == 0


def test_non_string_message_passes() -> None:
    """非文字列型の`last_assistant_message`は判定を通過する。"""
    result = _run({"last_assistant_message": None})
    assert result.stdout == ""
    assert result.returncode == 0


def _run_with_state_dir(payload: dict, state_dir: Path) -> subprocess.CompletedProcess[str]:
    """`session_state.py`の状態ファイル配置先を`state_dir`へ切り替えて`subagent_stop_advisor.py`を実行する。"""
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("subagent_stop_advisor",),
        input=json.dumps(payload),
        env=env,
    )


def _write_flag_state(state_dir: Path, session_id: str, sub_session_id: str, subagent_type: str = "plan-impl-executor") -> None:
    """`plan_impl_executor_active_subagent_sessions`フラグを事前に書き込む。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    state_path.write_text(
        json.dumps(
            {
                "plan_impl_executor_active_subagent_sessions": {
                    sub_session_id: {"subagent_type": subagent_type, "started_at": 0.0},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _transcript_path_for(tmp_path: Path, agent_id: str) -> str:
    """`agent_id`に対応する実装経路fixture入りtranscriptを生成する。"""
    directory = tmp_path / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"agent-{agent_id}.jsonl"
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in _implementation_route_entries()) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _valid_escalation_blockers() -> str:
    """2試行の反復失敗を表す構造化blockerを返す。"""
    return """- blocker_type: repeated_failure
  blocker_operation: tests
  blocker_evidence:
  - operation_key: tests
    attempt_number: 1
    evidence_id: toolu_failure_1
    tool_use_id: toolu_failure_1
    input:
      operation_key: tests
      command: pytest
    result: failed
    terminal_state: failed
  - operation_key: tests
    attempt_number: 2
    evidence_id: toolu_failure_2
    tool_use_id: toolu_failure_2
    input:
      operation_key: tests
      command: pytest
    result: failed
    terminal_state: failed
  blocker_attempts: 2"""


def _inactive_route_blocker(blocker_type: str, terminal_state: str) -> str:
    """未開始または利用不能経路の単一試行blockerを返す。"""
    return f"""- blocker_type: {blocker_type}
  blocker_operation: implementation-route
  blocker_evidence:
  - operation_key: implementation-route
    attempt_number: 1
    evidence_id: implementation-route
    tool_use_id: なし
    input:
      {"missing_key" if blocker_type == "missing_input" else "route_key"}: implementation-route
    result: {terminal_state}
    terminal_state: {terminal_state}
  blocker_attempts: 1"""


def _single_blocker(blocker_type: str, terminal_state: str) -> str:
    """単一試行の構造化blockerを返す。"""
    return f"""- blocker_type: {blocker_type}
  blocker_operation: operation
  blocker_evidence:
  - operation_key: operation
    attempt_number: 1
    evidence_id: toolu_operation
    tool_use_id: toolu_operation
    input:
      operation_key: operation
      value: input
    result: result
    terminal_state: {terminal_state}
  blocker_attempts: 1"""


def _confirmation_blocker(blocker_type: str, confirmation_key: str) -> str:
    """利用者確認待ちのツール未実行blockerを返す。"""
    return f"""- blocker_type: {blocker_type}
  blocker_operation: confirmation
  blocker_evidence:
  - operation_key: confirmation
    attempt_number: 1
    evidence_id: {confirmation_key}
    tool_use_id: なし
    input:
      confirmation_key: {confirmation_key}
    result: awaiting user response
    terminal_state: awaiting_confirmation
  blocker_attempts: 1"""


def _repository_change_blocker(comparison_sha: str) -> str:
    """比較SHAを観測識別子とするツール未実行blockerを返す。"""
    return f"""- blocker_type: repository_change
  blocker_operation: compare-head
  blocker_evidence:
  - operation_key: compare-head
    attempt_number: 1
    evidence_id: {comparison_sha}
    tool_use_id: なし
    input:
      comparison_sha: {comparison_sha}
      expected_sha: 1111111
    result:
      actual_sha: 2222222
    terminal_state: changed
  blocker_attempts: 1"""


def _target_expansion_blocker(path_count: int) -> str:
    paths = ", ".join(f"path-{index}" for index in range(path_count))
    return f"""- blocker_type: target_expansion
  blocker_operation: expand-targets
  blocker_evidence:
  - operation_key: expand-targets
    attempt_number: 1
    evidence_id: toolu_compare
    tool_use_id: toolu_compare
    input:
      operation_key: expand-targets
      previous_paths: []
      current_added_paths: [{paths}]
    result:
      deduplicated_paths: [{paths}]
    terminal_state: threshold_reached
  blocker_attempts: 1"""


def _run_tracked_report(tmp_path: Path, report: str, *, agent_id: str = "contract-agent") -> subprocess.CompletedProcess[str]:
    """追跡済みexecutorの報告を実装経路fixture付きで検査する。"""
    session_id = f"sid-{agent_id}"
    _write_flag_state(tmp_path, session_id, agent_id)
    return _run_with_state_dir(
        {
            "session_id": session_id,
            "agent_id": agent_id,
            "last_assistant_message": report,
            "agent_transcript_path": _transcript_path_for(tmp_path, agent_id),
        },
        tmp_path,
    )


def _append_transcript_entries(path: str, entries: list[dict]) -> None:
    """実装経路fixtureへ追加のtool use/resultを追記する。"""
    transcript = Path(path)
    with transcript.open("a", encoding="utf-8") as stream:
        for entry in entries:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _complete_report(**overrides: str) -> str:
    """`plan-impl-executor`「出力」節の主要欄を全て含む雛形報告を返す。"""
    fields = {
        "status": "completed",
        "summary": "全変更を反映",
        "changed": "- [x] item — /path",
        "external_operations": "- operation: なし\n  target: なし\n  result: not_applicable\n  evidence: なし",
        "verification": "- `pytest` — pass",
        "commit_sha": "abc123",
        "review_status": "実施完了（計画準拠系採用0件・独立系採用0件）",
        "review_final_findings": "計画準拠系0件・独立系0件",
        "review_skip_instruction": "なし",
        "review_caller_verification": "不要",
        "pending_confirmations": "なし",
        "plan_gaps": "なし",
        "applied_instructions": "なし",
        "implementation_thread_id": "th_impl",
        "plan_review_thread_id": "th_plan_review",
        "independent_review_thread_id": "th_independent_review",
        "implementation_agent_id": "なし",
        "plan_review_agent_id": "なし",
        "independent_review_agent_id": "なし",
        "implementation_route": "codex",
        "implementation_route_evidence": "- tool_name: mcp__codex__codex\n  tool_use_id: toolu_impl\n  route_id: th_impl",
        "plan_review_route": "codex",
        "independent_review_route": "codex",
        "review_rounds": "1",
        "review_coverage": "外部境界 | CLI入出力 | 0件",
        "review_impact_audit": "指摘なし",
        "implementation_history": "実装完了",
        "plan_review_history": "指摘なし",
        "independent_review_history": "指摘なし",
        "review_resolution": "指摘なし",
        "blockers": "- なし",
    }
    fields.update(overrides)
    if "implementation_route_evidence" not in overrides:
        if fields["implementation_route"] == "codex":
            fields["implementation_route_evidence"] = (
                "- tool_name: mcp__codex__codex\n  tool_use_id: toolu_impl\n  route_id: " + fields["implementation_thread_id"]
            )
        elif fields["implementation_route"] == "claude":
            fields["implementation_route_evidence"] = (
                "- tool_name: Agent\n  tool_use_id: toolu_impl_agent\n  route_id: " + fields["implementation_agent_id"]
            )
        else:
            fields["implementation_route_evidence"] = "- なし"
    if "blockers" not in overrides and fields["status"] == "needs_escalation":
        if fields["implementation_route"] == "not_started":
            fields["blockers"] = _inactive_route_blocker("missing_input", "not_started")
        elif fields["implementation_route"] == "unavailable":
            fields["blockers"] = _inactive_route_blocker("route_unavailable", "unavailable")
        else:
            fields["blockers"] = _valid_escalation_blockers()
    if "review_final_findings" not in overrides:
        if fields["status"] == "needs_escalation":
            fields["review_final_findings"] = "未確定"
        elif fields["review_status"] == "レビューは実施しない（ユーザー指示）":
            fields["review_final_findings"] = "対象外"
    if "review_skip_instruction" not in overrides and fields["review_status"] == "レビューは実施しない（ユーザー指示）":
        fields["review_skip_instruction"] = "レビューを省略すること"
    if "review_caller_verification" not in overrides:
        if fields["status"] == "needs_escalation":
            fields["review_caller_verification"] = "未完了事項の確認が必要"
        elif fields["review_status"] == "レビューは実施しない（ユーザー指示）":
            fields["review_caller_verification"] = "ユーザー指示原文との照合が必要"
    if fields["review_status"] == "レビューは実施しない（ユーザー指示）":
        if "review_coverage" not in overrides:
            fields["review_coverage"] = "なし"
        if "review_impact_audit" not in overrides:
            fields["review_impact_audit"] = "なし"
    if fields["status"] == "needs_escalation" and fields["review_status"] == "レビュー未完了":
        # レビュー工程へ到達していない状態の整合値。矛盾を検査するテストはoverridesで上書きする。
        for label, value in (
            ("review_rounds", "0"),
            ("review_resolution", "なし"),
            ("review_coverage", "なし"),
            ("review_impact_audit", "なし"),
            ("plan_review_history", "なし"),
            ("independent_review_history", "なし"),
            ("plan_review_route", "not_started"),
            ("independent_review_route", "not_started"),
            ("plan_review_thread_id", "なし"),
            ("independent_review_thread_id", "なし"),
        ):
            if label not in overrides:
                fields[label] = value
    return "\n".join(f"{k}: {v}" if not v.startswith("-") else f"{k}:\n{v}" for k, v in fields.items())


class TestPlanImplExecutorReportFormat:
    """`plan-impl-executor`完了報告本文の主要欄ラベル存在検査。"""

    def test_flag_not_registered_passes_without_check(self, tmp_path: Path) -> None:
        """フラグ未登録時は書式検査を発火せず通過する。"""
        result = _run_with_state_dir(
            {
                "session_id": "sid-format-no-flag",
                "last_assistant_message": "実装完了",
                "transcript_path": _transcript_path_for(tmp_path, "sub-none"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_complete_report_passes(self, tmp_path: Path) -> None:
        """主要欄が全て含まれる報告は通過する。"""
        sid = "sid-format-complete"
        _write_flag_state(tmp_path, sid, "sub-a")
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(),
                "agent_id": "sub-a",
                "transcript_path": _transcript_path_for(tmp_path, "sub-a"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert "sub-a" in state["plan_impl_executor_active_subagent_sessions"]

    def test_registered_executor_is_checked_before_pending_descendant(self, tmp_path: Path) -> None:
        sid = "sid-pending-invalid"
        agent_id = "sub-pending-invalid"
        _write_flag_state(tmp_path, sid, agent_id)
        parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry("toolu_pending_invalid")])
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_id": agent_id,
                "last_assistant_message": "status: completed",
                "transcript_path": parent,
                "agent_transcript_path": agent,
            },
            tmp_path,
        )
        assert json.loads(result.stdout)["decision"] == "block"
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in state["plan_impl_executor_active_subagent_sessions"]

    def test_registered_executor_success_with_pending_descendant_keeps_active_entry(self, tmp_path: Path) -> None:
        sid = "sid-pending-valid"
        agent_id = "sub-pending-valid"
        _write_flag_state(tmp_path, sid, agent_id)
        parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry("toolu_pending_valid")])
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_id": agent_id,
                "last_assistant_message": _complete_report(),
                "transcript_path": parent,
                "agent_transcript_path": agent,
            },
            tmp_path,
        )
        assert result.stdout == ""
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in state["plan_impl_executor_active_subagent_sessions"]

    def test_registered_executor_final_report_is_checked_after_pending_descendant_completes(self, tmp_path: Path) -> None:
        sid = "sid-pending-completes"
        agent_id = "sub-pending-completes"
        tool_id = "toolu_pending_completes"
        _write_flag_state(tmp_path, sid, agent_id)
        parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry(tool_id)])
        common = {
            "session_id": sid,
            "agent_id": agent_id,
            "transcript_path": parent,
            "agent_transcript_path": agent,
        }
        first = _run_with_state_dir({**common, "last_assistant_message": _complete_report()}, tmp_path)
        assert first.stdout == ""
        Path(agent).write_text(
            "\n".join(
                json.dumps(entry, ensure_ascii=False)
                for entry in [_user_async_launched_entry(tool_id), _user_task_notification_entry(tool_id)]
            )
            + "\n",
            encoding="utf-8",
        )
        invalid = _run_with_state_dir({**common, "last_assistant_message": "status: completed"}, tmp_path)
        assert json.loads(invalid.stdout)["decision"] == "block"
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in state["plan_impl_executor_active_subagent_sessions"]
        retry = _run_with_state_dir(
            {**common, "last_assistant_message": _complete_report(), "stop_hook_active": True}, tmp_path
        )
        assert json.loads(retry.stdout)["decision"] == "approve"
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in state["plan_impl_executor_active_subagent_sessions"]

    @pytest.mark.parametrize(("valid", "pending"), [(False, False), (True, True)])
    def test_registered_executor_stop_hook_retry_keeps_active_entry_for_invalid_or_pending_report(
        self, tmp_path: Path, valid: bool, pending: bool
    ) -> None:
        sid = f"sid-retry-keeps-{valid}-{pending}"
        agent_id = f"sub-retry-keeps-{valid}-{pending}"
        _write_flag_state(tmp_path, sid, agent_id)
        payload = {
            "session_id": sid,
            "agent_id": agent_id,
            "last_assistant_message": _complete_report() if valid else "status: completed",
            "stop_hook_active": True,
        }
        if pending:
            parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry("toolu_retry_pending")])
            payload.update({"transcript_path": parent, "agent_transcript_path": agent})
        result = _run_with_state_dir(payload, tmp_path)
        assert json.loads(result.stdout)["decision"] == "approve"
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in state["plan_impl_executor_active_subagent_sessions"]

    def test_registered_executor_stop_hook_retry_keeps_valid_final_report_active(self, tmp_path: Path) -> None:
        sid = "sid-retry-consumes"
        agent_id = "sub-retry-consumes"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "agent_id": agent_id,
                "last_assistant_message": _complete_report(),
                "stop_hook_active": True,
            },
            tmp_path,
        )
        assert json.loads(result.stdout)["decision"] == "approve"
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in state["plan_impl_executor_active_subagent_sessions"]

    def test_unregistered_agent_with_pending_descendant_is_approved(self, tmp_path: Path) -> None:
        parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry("toolu_unregistered")])
        result = _run_with_state_dir(
            {
                "session_id": "sid-unregistered-pending",
                "agent_id": "sub-unregistered-pending",
                "last_assistant_message": "",
                "transcript_path": parent,
                "agent_transcript_path": agent,
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_review_value_mismatch_passes_to_caller(self, tmp_path: Path) -> None:
        """レビュー値の不整合はhookで差し戻さず呼び出し元の検収へ渡す。"""
        sid = "sid-format-review-value-mismatch"
        agent_id = "sub-review-value-mismatch"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(
                    status="completed_with_review_cap",
                    review_status="実施完了（計画準拠系採用0件・独立系採用0件）",
                    review_rounds="4",
                ),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_missing_label_blocks(self, tmp_path: Path) -> None:
        """主要欄が欠落する報告はblockし理由文に欠落ラベルを列挙する。"""
        sid = "sid-format-missing"
        _write_flag_state(tmp_path, sid, "sub-b")
        report = _complete_report()
        # `plan_gaps:`行を除去する
        report = "\n".join(line for line in report.splitlines() if not line.startswith("plan_gaps"))
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-b"),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "plan_gaps" in body["reason"]
        assert "plan-impl-caller-reception.md" in body["reason"]

    def test_missing_label_blocks_and_preserves_entry_for_retry(self, tmp_path: Path) -> None:
        """書式不備でblockした場合、状態辞書のエントリは削除されず再試行時も検査対象のままである。"""
        sid = "sid-format-missing-retry"
        _write_flag_state(tmp_path, sid, "sub-retry")
        report = _complete_report()
        report = "\n".join(line for line in report.splitlines() if not line.startswith("plan_gaps"))
        _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-retry"),
            },
            tmp_path,
        )
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "sub-retry" in state.get("plan_impl_executor_active_subagent_sessions", {})

    def test_missing_external_operations_label_blocks(self, tmp_path: Path) -> None:
        """`external_operations`欄が欠落する報告はblockし理由文に当該ラベルを列挙する。"""
        sid = "sid-format-missing-external-operations"
        _write_flag_state(tmp_path, sid, "sub-e")
        report = _complete_report()
        report = "\n".join(line for line in report.splitlines() if not line.startswith("external_operations"))
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-e"),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "external_operations" in body["reason"]

    def test_missing_applied_instructions_label_blocks(self, tmp_path: Path) -> None:
        """`applied_instructions`欄が欠落する報告はblockし理由文に当該ラベルを列挙する。"""
        sid = "sid-format-missing-applied-instructions"
        _write_flag_state(tmp_path, sid, "sub-i")
        report = _complete_report()
        report = "\n".join(line for line in report.splitlines() if not line.startswith("applied_instructions"))
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-i"),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "applied_instructions" in body["reason"]

    @pytest.mark.parametrize(
        "missing_label",
        [
            "review_final_findings",
            "review_skip_instruction",
            "review_caller_verification",
            "plan_review_thread_id",
            "independent_review_thread_id",
            "plan_review_history",
            "independent_review_history",
            "review_coverage",
            "review_impact_audit",
        ],
    )
    def test_missing_review_track_label_blocks(self, tmp_path: Path, missing_label: str) -> None:
        """いずれかのレビュー系必須欄が欠落する報告をblockする。"""
        sid = f"sid-format-missing-{missing_label}"
        agent_id = f"sub-{missing_label}"
        _write_flag_state(tmp_path, sid, agent_id)
        report = "\n".join(line for line in _complete_report().splitlines() if not line.startswith(f"{missing_label}:"))
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert missing_label in body["reason"]

    def test_unchecked_item_inside_applied_instructions_does_not_block(self, tmp_path: Path) -> None:
        """`changed`欄の境界検査は`applied_instructions`欄の追加後も正しく`changed`欄末尾で止まる。"""
        sid = "sid-format-applied-instructions-boundary"
        _write_flag_state(tmp_path, sid, "sub-j")
        report = _complete_report(
            changed="- [x] item — /path（run_in_background=trueで並列起動）",
            applied_instructions="- [ ] チェックボックス形式だが検査対象外の記述",
        )
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-j"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_needs_escalation_requires_blockers(self, tmp_path: Path) -> None:
        """`status: needs_escalation`検出時は`blockers`欄も必須。"""
        sid = "sid-format-needs-escalation"
        _write_flag_state(tmp_path, sid, "sub-c")
        report = _complete_report(status="needs_escalation").split("\nblockers:", maxsplit=1)[0]
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-c"),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "blockers" in body["reason"]

    def test_needs_escalation_with_blockers_passes(self, tmp_path: Path) -> None:
        """`status: needs_escalation`かつ`blockers`欄あり報告は通過する。"""
        sid = "sid-format-escalation-ok"
        _write_flag_state(tmp_path, sid, "sub-d")
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            plan_review_route="unavailable",
            plan_review_thread_id="なし",
        )
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-d"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_flag_entries_are_retained_after_check(self, tmp_path: Path) -> None:
        """SubagentStop発火後も該当agentIdと他の並行エントリを保持する。"""
        sid = "sid-format-cleanup"
        _write_flag_state(tmp_path, sid, "sub-e")
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["plan_impl_executor_active_subagent_sessions"]["sub-other"] = {
            "subagent_type": "plan-impl-executor",
            "started_at": 0.0,
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(),
                "transcript_path": _transcript_path_for(tmp_path, "sub-e"),
            },
            tmp_path,
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        active = state.get("plan_impl_executor_active_subagent_sessions")
        assert "sub-e" in active
        assert "sub-other" in active

    def test_background_parallel_declaration_with_unchecked_item_blocks(self, tmp_path: Path) -> None:
        """FB[3]: background並列起動宣言と`changed`欄未消化項目が共起する完了報告をblockする。"""
        sid = "sid-format-bg-violation"
        _write_flag_state(tmp_path, sid, "sub-f")
        report = _complete_report(changed="- [ ] item — /path（run_in_background=trueで並列起動）")
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-f"),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "agent-toolkit/rules/02-claude-code.md" in body["reason"]
        assert "サブエージェント運用" in body["reason"]
        assert "run_in_background" in body["reason"]
        assert "実行結果" in body["reason"]
        assert "foreground" not in body["reason"]

    def test_background_parallel_declaration_with_all_checked_passes(self, tmp_path: Path) -> None:
        """全項目チェック済みならbackground並列起動宣言があっても通過する。"""
        sid = "sid-format-bg-ok"
        _write_flag_state(tmp_path, sid, "sub-g")
        report = _complete_report(changed="- [x] item — /path（run_in_background=trueで並列起動）")
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-g"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_unchecked_item_outside_changed_section_does_not_block(self, tmp_path: Path) -> None:
        """FB[3]是正: `changed`欄以外の未チェック項目はbackground並列起動宣言と共起しても誤ってblockしない。"""
        sid = "sid-format-bg-outside-changed"
        _write_flag_state(tmp_path, sid, "sub-h")
        report = _complete_report(
            changed="- [x] item — /path（run_in_background=trueで並列起動）",
            verification="- [ ] 未解決の論点",
        )
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-h"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_unregistered_agent_id_skips_check_and_preserves_other_entries(self, tmp_path: Path) -> None:
        """未登録のagentIdからの完了報告は書式検査を発火せず、登録済み他エントリも保持する（FB[9]）。"""
        sid = "sid-format-agent-id-mismatch"
        _write_flag_state(tmp_path, sid, "sub-registered")
        report = "status: completed\nsummary: 別種別サブエージェントの完了報告"
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, "sub-unregistered"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "sub-registered" in state.get("plan_impl_executor_active_subagent_sessions", {})

    def test_transcript_path_missing_skips_check(self, tmp_path: Path) -> None:
        """`transcript_path`欠落時は書式検査を発火しない（安全側、FB[9]）。"""
        sid = "sid-format-no-transcript"
        _write_flag_state(tmp_path, sid, "sub-z")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": "status: completed"},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    def test_filename_partial_match_does_not_extract_agent_id(self, tmp_path: Path) -> None:
        """ファイル名途中に`agent-<id>.jsonl`を含むだけの文字列からは`agentId`を抽出しない。

        `not-agent-sub-a.jsonl`は`sub-a`を含むが、先頭一致（`^agent-...`）を満たさないため
        登録済み`sub-a`との突合は成立せず検査を発火しない。
        """
        sid = "sid-format-partial-match"
        _write_flag_state(tmp_path, sid, "sub-a")
        report = "status: completed\nsummary: 途中一致の誤抽出防止確認"
        transcript = str(tmp_path / "subagents" / "not-agent-sub-a.jsonl")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report, "transcript_path": transcript},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "sub-a" in state.get("plan_impl_executor_active_subagent_sessions", {})

    def test_directory_component_match_does_not_extract_agent_id(self, tmp_path: Path) -> None:
        """パスのディレクトリ部分に`agent-<id>.jsonl`形式が現れてもbasenameのみで照合する。

        `.../agent-sub-a.jsonl/unrelated.jsonl`はディレクトリ部分に有効形式を含むが、
        basename（`unrelated.jsonl`）は`agent-<id>.jsonl`形式に一致しないため抽出しない。
        """
        sid = "sid-format-dir-component-match"
        _write_flag_state(tmp_path, sid, "sub-a")
        report = "status: completed\nsummary: ディレクトリ部分一致の誤抽出防止確認"
        transcript = str(tmp_path / "subagents" / "agent-sub-a.jsonl" / "unrelated.jsonl")
        result = _run_with_state_dir(
            {"session_id": sid, "last_assistant_message": report, "transcript_path": transcript},
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0
        state_path = tmp_path / f"claude-agent-toolkit-{sid}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "sub-a" in state.get("plan_impl_executor_active_subagent_sessions", {})


class TestPlanImplExecutorEvidenceContract:
    """構造化blockerと実装系統証跡の公開入口検査。"""

    @pytest.mark.parametrize(
        ("blocker_type", "overrides", "blockers"),
        [
            (
                "missing_input",
                {
                    "implementation_route": "not_started",
                    "implementation_thread_id": "なし",
                    "implementation_agent_id": "なし",
                },
                _inactive_route_blocker("missing_input", "not_started"),
            ),
            (
                "user_decision",
                {"pending_confirmations": "decision-key"},
                _confirmation_blocker("user_decision", "decision-key"),
            ),
            (
                "destructive_action",
                {"pending_confirmations": "destructive-key"},
                _confirmation_blocker("destructive_action", "destructive-key"),
            ),
            ("repository_change", {}, _repository_change_blocker("abcdef1")),
            ("recovery_failure", {}, _single_blocker("recovery_failure", "failed")),
        ],
    )
    def test_single_attempt_blocker_types_pass(
        self,
        tmp_path: Path,
        blocker_type: str,
        overrides: dict[str, str],
        blockers: str,
    ) -> None:
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            blockers=blockers,
            **overrides,
        )
        assert _run_tracked_report(tmp_path, report, agent_id=blocker_type).stdout == ""

    def test_route_unavailable_blocker_passes(self, tmp_path: Path) -> None:
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            implementation_route="unavailable",
            implementation_thread_id="なし",
            implementation_agent_id="なし",
            blockers=_inactive_route_blocker("route_unavailable", "unavailable"),
        )
        assert _run_tracked_report(tmp_path, report, agent_id="route-unavailable").stdout == ""

    def test_repeated_failure_requires_two_attempts(self, tmp_path: Path) -> None:
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            blockers=_single_blocker("repeated_failure", "failed"),
        )
        result = _run_tracked_report(tmp_path, report, agent_id="repeated-one")
        assert json.loads(result.stdout)["decision"] == "block"
        assert "at least 2 attempts" in result.stdout

    @pytest.mark.parametrize(("path_count", "passes"), [(4, False), (5, True)])
    def test_target_expansion_threshold(self, tmp_path: Path, path_count: int, passes: bool) -> None:
        report = _complete_report(
            status="needs_escalation",
            review_status="対象拡大により中断（指摘反映済み・再レビューなし）",
            review_final_findings="計画準拠系0件・独立系0件",
            blockers=_target_expansion_blocker(path_count),
        )
        result = _run_tracked_report(tmp_path, report, agent_id=f"target-{path_count}")
        assert (result.stdout == "") is passes

    @pytest.mark.parametrize(
        ("blockers", "expected"),
        [
            (
                _valid_escalation_blockers().replace("blocker_attempts: 2", "blocker_attempts: 1"),
                "unique evidence count",
            ),
            (
                _valid_escalation_blockers().replace("attempt_number: 2", "attempt_number: 1"),
                "contiguous from 1",
            ),
        ],
    )
    def test_attempt_count_and_sequence_mismatch_block(
        self,
        tmp_path: Path,
        blockers: str,
        expected: str,
    ) -> None:
        report = _complete_report(status="needs_escalation", review_status="レビュー未完了", blockers=blockers)
        result = _run_tracked_report(tmp_path, report, agent_id=f"attempt-{len(expected)}")
        assert json.loads(result.stdout)["decision"] == "block"
        assert expected in result.stdout

    @pytest.mark.parametrize(
        ("source", "replacement", "expected"),
        [
            ("command: pytest", "command: fictitious", "input does not match the transcript tool input"),
            ("result: failed", "result: fictitious", "result does not match the transcript tool result"),
        ],
    )
    def test_fictitious_tool_blocker_evidence_blocks(
        self,
        tmp_path: Path,
        source: str,
        replacement: str,
        expected: str,
    ) -> None:
        """JSONLと異なるtool入出力を申告したblocker証跡を拒否する。"""
        blockers = _valid_escalation_blockers().replace(source, replacement, 1)
        report = _complete_report(status="needs_escalation", review_status="レビュー未完了", blockers=blockers)
        result = _run_tracked_report(tmp_path, report, agent_id="fictitious-blocker")
        assert json.loads(result.stdout)["decision"] == "block"
        assert expected in result.stdout

    def test_confirmation_blocker_must_match_same_pending_item(self, tmp_path: Path) -> None:
        """確認blockerと異なるpending項目を申告した報告を拒否する。"""
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            pending_confirmations="different-key",
            blockers=_confirmation_blocker("user_decision", "decision-key"),
        )
        result = _run_tracked_report(tmp_path, report, agent_id="pending-mismatch")
        assert json.loads(result.stdout)["decision"] == "block"
        assert "same item in pending_confirmations" in result.stdout

    def test_confirmation_item_must_appear_in_observed_tool_input(self, tmp_path: Path) -> None:
        """実在tool入力と無関係なpending確認キーの後付けを拒否する。"""
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            pending_confirmations="toolu_operation",
            blockers=_single_blocker("user_decision", "awaiting_confirmation"),
        )
        result = _run_tracked_report(tmp_path, report, agent_id="pending-unrelated-input")
        assert json.loads(result.stdout)["decision"] == "block"
        assert "same item in pending_confirmations" in result.stdout

    def test_repository_change_requires_observable_comparison_sha(self, tmp_path: Path) -> None:
        """比較SHAでないツール未実行repository_change証跡を拒否する。"""
        report = _complete_report(
            status="needs_escalation",
            review_status="レビュー未完了",
            blockers=_repository_change_blocker("not-a-sha"),
        )
        result = _run_tracked_report(tmp_path, report, agent_id="invalid-comparison-sha")
        assert json.loads(result.stdout)["decision"] == "block"
        assert "hexadecimal commit identifier" in result.stdout

    def test_codex_route_id_mismatch_blocks(self, tmp_path: Path) -> None:
        report = _complete_report(
            implementation_route_evidence=(
                "- tool_name: mcp__codex__codex\n  tool_use_id: toolu_impl\n  route_id: wrong-thread"
            )
        )
        result = _run_tracked_report(tmp_path, report, agent_id="codex-mismatch")
        assert json.loads(result.stdout)["decision"] == "block"
        assert "implementation identity" in result.stdout

    def test_codex_reply_on_same_thread_passes(self, tmp_path: Path) -> None:
        agent_id = "codex-reply"
        session_id = f"sid-{agent_id}"
        _write_flag_state(tmp_path, session_id, agent_id)
        transcript = _transcript_path_for(tmp_path, agent_id)
        _append_transcript_entries(
            transcript,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_reply",
                                "name": "mcp__codex__codex-reply",
                                "input": {"threadId": "th_impl", "prompt": "continue"},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_reply",
                                "content": json.dumps({"threadId": "th_impl"}),
                            }
                        ]
                    },
                },
            ],
        )
        evidence = """- tool_name: mcp__codex__codex
  tool_use_id: toolu_impl
  route_id: th_impl
- tool_name: mcp__codex__codex-reply
  tool_use_id: toolu_reply
  route_id: th_impl"""
        result = _run_with_state_dir(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "last_assistant_message": _complete_report(implementation_route_evidence=evidence),
                "agent_transcript_path": transcript,
            },
            tmp_path,
        )
        assert result.stdout == ""

    def test_omitted_codex_reply_evidence_blocks(self, tmp_path: Path) -> None:
        """JSONL上の実装系replyを証跡集合から省いた報告を拒否する。"""
        agent_id = "codex-reply-omitted"
        session_id = f"sid-{agent_id}"
        _write_flag_state(tmp_path, session_id, agent_id)
        transcript = _transcript_path_for(tmp_path, agent_id)
        _append_transcript_entries(
            transcript,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_reply_omitted",
                                "name": "mcp__codex__codex-reply",
                                "input": {"threadId": "th_impl", "prompt": "continue"},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_reply_omitted",
                                "content": json.dumps({"threadId": "th_impl"}),
                            }
                        ]
                    },
                },
            ],
        )
        result = _run_with_state_dir(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "last_assistant_message": _complete_report(),
                "agent_transcript_path": transcript,
            },
            tmp_path,
        )
        assert json.loads(result.stdout)["decision"] == "block"
        assert "must match all implementation calls" in result.stdout

    def test_sendmessage_on_same_agent_passes(self, tmp_path: Path) -> None:
        agent_id = "claude-resume"
        session_id = f"sid-{agent_id}"
        _write_flag_state(tmp_path, session_id, agent_id)
        transcript = _transcript_path_for(tmp_path, agent_id)
        _append_transcript_entries(
            transcript,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_send",
                                "name": "SendMessage",
                                "input": {"to": "agent-implementation", "message": "continue"},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_send",
                                "content": json.dumps({"pin": {"id": "agent-implementation"}}),
                            }
                        ]
                    },
                },
            ],
        )
        evidence = """- tool_name: Agent
  tool_use_id: toolu_impl_agent
  route_id: agent-implementation
- tool_name: SendMessage
  tool_use_id: toolu_send
  route_id: agent-implementation"""
        report = _complete_report(
            implementation_route="claude",
            implementation_thread_id="なし",
            implementation_agent_id="agent-implementation",
            implementation_route_evidence=evidence,
        )
        result = _run_with_state_dir(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "last_assistant_message": report,
                "agent_transcript_path": transcript,
            },
            tmp_path,
        )
        assert result.stdout == ""

    def test_review_track_identity_cannot_satisfy_implementation(self, tmp_path: Path) -> None:
        agent_id = "review-reuse"
        session_id = f"sid-{agent_id}"
        _write_flag_state(tmp_path, session_id, agent_id)
        transcript = _transcript_path_for(tmp_path, agent_id)
        _append_transcript_entries(
            transcript,
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_review",
                                "name": "mcp__codex__codex",
                                "input": {"prompt": "execution_track: plan_review"},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_review",
                                "content": json.dumps({"threadId": "th_impl"}),
                            }
                        ]
                    },
                },
            ],
        )
        result = _run_with_state_dir(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "last_assistant_message": _complete_report(),
                "agent_transcript_path": transcript,
            },
            tmp_path,
        )
        assert json.loads(result.stdout)["decision"] == "block"
        assert "review track" in result.stdout
