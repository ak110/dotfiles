"""subagent_stop_advisorのテスト。

scope-escalation検出テストの入力フレーズは
`agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt`
から動的に読み込む（`agent-toolkit:agent-standards`「完成条件」節。
検出語そのものをテストコード本文へ転記しない）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import _fork_runner
import pytest
from _scope_escalation_test_helpers import load_scope_escalation_inputs
from _stop_gate_test import (
    _user_async_launched_entry,
    _user_background_bash_entry,
    _user_task_notification_entry,
    _write_transcript,
)

_SCRIPT = Path(__file__).parent / "claude_hook.py"

_SCOPE_ESCALATION_INPUTS = load_scope_escalation_inputs()


def _pick_scope_escalation_text(category: str) -> str:
    """指定カテゴリの最小マッチ入力を1件返す。フィクスチャ不在時は空文字列。

    フィクスチャ内の最後の該当行を返す。新規追記した最小マッチ入力を
    優先的にE2Eテストへ供給するため（末尾追記が既定の追記位置のため）。
    """
    picked = ""
    for text, cat in _SCOPE_ESCALATION_INPUTS:
        if cat == category:
            picked = text
    return picked


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    return _fork_runner.run_script(_SCRIPT, argv=("subagent_stop_advisor",), input=json.dumps(payload))


def _write_hook_transcripts(tmp_path: Path, parent_entries: list[dict], agent_entries: list[dict]) -> tuple[str, str]:
    """公式Hook入力と同じ親セッション記録と対象サブエージェント記録を生成する。"""
    parent_dir = tmp_path / "parent"
    agent_dir = tmp_path / "agent"
    parent_dir.mkdir()
    agent_dir.mkdir()
    parent = _write_transcript(parent_dir, parent_entries)
    agent = _write_transcript(agent_dir, agent_entries)
    return str(parent), str(agent)


@pytest.mark.parametrize(
    ("category", "message_override", "expected_decision", "expected_returncode"),
    [
        pytest.param(None, None, None, 0, id="no-message"),
        pytest.param(None, "工程4完了。次工程へ移行する。", None, None, id="normal-message"),
        pytest.param("process-omission", None, "block", None, id="process-omission"),
        pytest.param("approach-confirm", None, "block", None, id="approach-confirm"),
        pytest.param("subagent-hesitation", None, "block", None, id="subagent-hesitation"),
    ],
)
def test_message_gate_scenarios(
    category: str | None,
    message_override: str | None,
    expected_decision: str | None,
    expected_returncode: int | None,
) -> None:
    message = message_override
    if category is not None:
        message = _pick_scope_escalation_text(category)
        if not message:
            pytest.skip(f"scope-escalation fixture for {category} not available")

    payload = {} if message is None else {"last_assistant_message": message}
    result = _run(payload)
    if expected_decision is None:
        assert result.stdout == ""
    else:
        body = json.loads(result.stdout)
        assert body["decision"] == expected_decision
    if expected_returncode is not None:
        assert result.returncode == expected_returncode


def test_blocks_overhead_tradeoff_phrases() -> None:
    """`overhead-tradeoff`カテゴリのフレーズもblockする。"""
    text = _pick_scope_escalation_text("overhead-tradeoff")
    if not text:
        pytest.skip("scope-escalation fixture for overhead-tradeoff not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "overhead-tradeoff" in body["reason"]


def test_approves_when_descendant_background_tracked_regardless_of_message(tmp_path: Path) -> None:
    """対象サブエージェント記録に未消化の孫起動がある場合、完了報告本文によらず承認する。

    本文には未消化background起動が無ければ通常経路でブロックされるはずの
    `process-omission`フレーズを用いる。早期承認判定を外すとブロックされることを
    別テスト（`test_blocks_process_omission_without_tracked_background`）で確認しており、
    両者の対比で本テストが早期承認分岐自体を検証していることを担保する。
    """
    text = _pick_scope_escalation_text("process-omission")
    if not text:
        pytest.skip("scope-escalation fixture for process-omission not available")
    parent, agent = _write_hook_transcripts(tmp_path, [], [_user_async_launched_entry("toolu_bg1")])
    result = _run({"last_assistant_message": text, "transcript_path": parent, "agent_transcript_path": agent})
    assert result.stdout == ""


def test_parent_pending_agent_does_not_bypass_completion_report_check(tmp_path: Path) -> None:
    """親記録だけの未完了起動は兄弟または停止対象自身であり、早期承認の根拠にしない。"""
    text = _pick_scope_escalation_text("process-omission")
    if not text:
        pytest.skip("scope-escalation fixture for process-omission not available")
    parent, agent = _write_hook_transcripts(tmp_path, [_user_async_launched_entry("toolu_sibling")], [])
    result = _run({"last_assistant_message": text, "transcript_path": parent, "agent_transcript_path": agent})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


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


def test_blocks_process_omission_without_tracked_background() -> None:
    """未消化のbackground起動が無い場合は現行どおり縮退表明フレーズの照合が働く。"""
    text = _pick_scope_escalation_text("process-omission")
    if not text:
        pytest.skip("scope-escalation fixture for process-omission not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_blocks_when_tracked_background_completed(tmp_path: Path) -> None:
    """起動記録があっても完了通知で全消化済みなら通常の縮退表明照合が働く。"""
    text = _pick_scope_escalation_text("process-omission")
    if not text:
        pytest.skip("scope-escalation fixture for process-omission not available")
    entries = [_user_async_launched_entry("toolu_bg2"), _user_task_notification_entry("toolu_bg2")]
    parent, agent = _write_hook_transcripts(tmp_path, [], entries)
    result = _run({"last_assistant_message": text, "transcript_path": parent, "agent_transcript_path": agent})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_stop_hook_active_bypasses_check() -> None:
    """`stop_hook_active`真は判定処理をせず無条件approveを返す。

    通常なら縮退表明としてblockされる本文であっても、再呼び出し時は
    連続ブロック上限による強制終了を避けるため無条件approveを返す。
    """
    text = _pick_scope_escalation_text("approach-confirm")
    if not text:
        pytest.skip("scope-escalation fixture for approach-confirm not available")
    result = _run({"last_assistant_message": text, "stop_hook_active": True})
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


def _write_finalizer_state(state_dir: Path, session_id: str, agent_id: str) -> None:
    """finalizerの活動中agentIdを事前に書き込む。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    state_path.write_text(
        json.dumps({"plan_file_finalizer_active_subagent_sessions": {agent_id: {"started_at": 0.0}}}),
        encoding="utf-8",
    )


def _transcript_path_for(tmp_path: Path, agent_id: str) -> str:
    """`agent_id`に対応するtranscriptパス文字列を生成する（実ファイルの存在は不要）。

    `_inspect_plan_impl_executor_report_format`はファイル名の`agent-<id>.jsonl`部分のみを
    参照しファイル内容を読み取らないため、実体作成は不要とする。
    """
    return str(tmp_path / "subagents" / f"agent-{agent_id}.jsonl")


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
        "plan_review_route": "codex",
        "independent_review_route": "codex",
        "review_rounds": "1",
        "review_coverage": "外部境界 | CLI入出力 | 0件",
        "review_impact_audit": "指摘なし",
        "implementation_history": "実装完了",
        "plan_review_history": "指摘なし",
        "independent_review_history": "指摘なし",
        "review_resolution": "指摘なし",
    }
    fields.update(overrides)
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
    return "\n".join(f"{k}: {v}" if not v.startswith("-") else f"{k}:\n{v}" for k, v in fields.items())


class TestPlanReviewCompletion:
    """背景実行されたfinalizerの計画レビュー完了追跡。"""

    def test_completed_report_sets_flag_and_consumes_entry(self, tmp_path: Path) -> None:
        sid = "sid-finalizer-completed"
        agent_id = "finalizer-completed"
        _write_finalizer_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": "status: completed\nreview_completed: true",
                "agent_id": agent_id,
                "transcript_path": str(tmp_path / f"{sid}.jsonl"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state["plan_review_completed"] is True
        assert not state["plan_file_finalizer_active_subagent_sessions"]

    @pytest.mark.parametrize(
        "report",
        [
            "status: needs_escalation\nreview_completed: true",
            "status: completed\nreview_completed: false",
            "status: completed\nreview_completed: true\nartifact: /tmp/result.md",
        ],
    )
    def test_incomplete_or_non_trailing_report_preserves_entry(self, tmp_path: Path, report: str) -> None:
        sid = f"sid-finalizer-invalid-{len(report)}"
        agent_id = "finalizer-invalid"
        _write_finalizer_state(tmp_path, sid, agent_id)
        _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "agent_transcript_path": _transcript_path_for(tmp_path, agent_id),
                "transcript_path": str(tmp_path / f"{sid}.jsonl"),
            },
            tmp_path,
        )
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state.get("plan_review_completed") is not True
        assert agent_id in state["plan_file_finalizer_active_subagent_sessions"]

    def test_unregistered_agent_does_not_set_flag(self, tmp_path: Path) -> None:
        sid = "sid-finalizer-unregistered"
        _write_finalizer_state(tmp_path, sid, "registered")
        _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": "status: completed\nreview_completed: true",
                "transcript_path": _transcript_path_for(tmp_path, "unregistered"),
            },
            tmp_path,
        )
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state.get("plan_review_completed") is not True
        assert "registered" in state["plan_file_finalizer_active_subagent_sessions"]

    def test_closing_fence_after_report_sets_flag(self, tmp_path: Path) -> None:
        sid = "sid-finalizer-fence"
        agent_id = "finalizer-fence"
        _write_finalizer_state(tmp_path, sid, agent_id)
        _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": "```text\nstatus: completed\nreview_completed: true\n```",
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state["plan_review_completed"] is True

    def test_empty_report_block_then_corrected_report_completes_review(self, tmp_path: Path) -> None:
        sid = "sid-finalizer-reentry"
        agent_id = "finalizer-reentry"
        _write_finalizer_state(tmp_path, sid, agent_id)
        payload = {
            "session_id": sid,
            "last_assistant_message": "",
            "transcript_path": _transcript_path_for(tmp_path, agent_id),
        }
        first = _run_with_state_dir(payload, tmp_path)
        assert json.loads(first.stdout)["decision"] == "block"
        blocked_state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert agent_id in blocked_state["plan_file_finalizer_active_subagent_sessions"]
        second = _run_with_state_dir(
            {
                **payload,
                "last_assistant_message": "status: completed\nreview_completed: true",
                "stop_hook_active": True,
            },
            tmp_path,
        )
        assert json.loads(second.stdout)["decision"] == "approve"
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert state["plan_review_completed"] is True
        assert not state["plan_file_finalizer_active_subagent_sessions"]


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
                "transcript_path": str(tmp_path / f"{sid}.jsonl"),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0
        state = json.loads((tmp_path / f"claude-agent-toolkit-{sid}.json").read_text(encoding="utf-8"))
        assert not state["plan_impl_executor_active_subagent_sessions"]

    @pytest.mark.parametrize(
        ("overrides", "expected_fragment"),
        [
            (
                {"review_status": "実施完了（計画準拠系採用0件・独立系採用0件）"},
                "review_status must show fixed known findings after review cap",
            ),
            ({"review_rounds": "4"}, "review_rounds must be 5 after review cap"),
            ({"review_coverage": "なし"}, "review_coverage must not be なし after review cap"),
            ({"review_impact_audit": "なし"}, "review_impact_audit must not be なし after review cap"),
        ],
    )
    def test_review_cap_value_mismatch_blocks(
        self,
        tmp_path: Path,
        overrides: dict[str, str],
        expected_fragment: str,
    ) -> None:
        """上限到達後の終端状態とreview_status・回数・証跡の矛盾をblockする。"""
        sid = f"sid-format-review-cap-mismatch-{len(expected_fragment)}"
        agent_id = f"sub-review-cap-mismatch-{len(expected_fragment)}"
        fields = {
            "status": "completed_with_review_cap",
            "review_status": "上限到達後の既知指摘修正済み（再レビューなし）",
            "review_rounds": "5",
        }
        fields.update(overrides)
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(**fields),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert expected_fragment in body["reason"]

    def test_review_cap_status_is_rejected_for_regular_completed_status(self, tmp_path: Path) -> None:
        """上限到達後専用のreview_statusを通常完了へ組み合わせた報告をblockする。"""
        sid = "sid-format-review-cap-status-with-completed"
        agent_id = "sub-review-cap-status-with-completed"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(review_status="上限到達後の既知指摘修正済み（再レビューなし）"),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "review_status must show completed review or user-directed skip" in body["reason"]

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

    @pytest.mark.parametrize(
        ("overrides", "expected_fragment"),
        [
            (
                {"plan_review_route": "not_started"},
                "plan_review_route must be codex or claude",
            ),
            (
                {"plan_review_thread_id": "なし"},
                "plan_review_thread_id must not be なし",
            ),
            (
                {"plan_review_route": "claude", "plan_review_thread_id": "th_invalid"},
                "plan_review_thread_id must be なし",
            ),
            (
                {"plan_review_route": "claude", "plan_review_thread_id": "なし"},
                "plan_review_agent_id must not be なし",
            ),
            (
                {"plan_review_agent_id": "agent-invalid"},
                "plan_review_agent_id must be なし",
            ),
            (
                {"review_rounds": "0"},
                "review_rounds must be between 1 and 5",
            ),
            (
                {"review_rounds": "6"},
                "review_rounds must be between 1 and 5",
            ),
            (
                {"review_final_findings": "計画準拠系-1件・独立系0件"},
                "review_final_findings must contain two non-negative finding counts",
            ),
            (
                {"review_final_findings": "計画準拠系x件・独立系0件"},
                "review_final_findings must contain two non-negative finding counts",
            ),
            (
                {"review_skip_instruction": "省略すること"},
                "review_skip_instruction must be なし",
            ),
            (
                {"review_caller_verification": "未完了事項の確認が必要"},
                "review_caller_verification must be 不要",
            ),
            (
                {"independent_review_history": "なし"},
                "independent_review_history must not be なし",
            ),
            (
                {"review_resolution": "なし"},
                "review_resolution must not be なし",
            ),
            (
                {"review_coverage": "なし"},
                "review_coverage must not be なし",
            ),
            (
                {"review_impact_audit": "なし"},
                "review_impact_audit must not be なし",
            ),
        ],
    )
    def test_completed_review_value_mismatch_blocks(
        self,
        tmp_path: Path,
        overrides: dict[str, str],
        expected_fragment: str,
    ) -> None:
        """実施完了時のroute・thread・round・履歴の矛盾をblockする。"""
        sid = f"sid-format-review-mismatch-{len(expected_fragment)}-{len(overrides)}"
        agent_id = f"sub-review-mismatch-{len(expected_fragment)}-{len(overrides)}"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(**overrides),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert expected_fragment in body["reason"]

    def test_completed_claude_routes_with_agent_ids_pass(self, tmp_path: Path) -> None:
        """Claude routeはthreadなし・Agent識別子ありの組み合わせを受理する。"""
        sid = "sid-format-claude-agent-ids"
        agent_id = "sub-claude-agent-ids"
        _write_flag_state(tmp_path, sid, agent_id)
        report = _complete_report(
            implementation_route="claude",
            implementation_thread_id="なし",
            implementation_agent_id="agent-implementation",
            plan_review_route="claude",
            plan_review_thread_id="なし",
            plan_review_agent_id="agent-plan-review",
            independent_review_route="claude",
            independent_review_thread_id="なし",
            independent_review_agent_id="agent-independent-review",
        )
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    @pytest.mark.parametrize("implementation_route", ["not_started", "unavailable"])
    def test_escalation_allows_unstarted_or_unavailable_implementation(
        self,
        tmp_path: Path,
        implementation_route: str,
    ) -> None:
        """実装開始前とAgent利用不能のエスカレーションを正当な状態として受理する。"""
        sid = f"sid-format-escalation-implementation-{implementation_route}"
        agent_id = f"sub-escalation-implementation-{implementation_route}"
        _write_flag_state(tmp_path, sid, agent_id)
        report = (
            _complete_report(
                status="needs_escalation",
                review_status="レビュー未完了",
                implementation_route=implementation_route,
                implementation_thread_id="なし",
                implementation_agent_id="なし",
                plan_review_route="not_started",
                plan_review_thread_id="なし",
                independent_review_route="not_started",
                independent_review_thread_id="なし",
            )
            + "\nblockers:\n- 未解決事項"
        )
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        assert result.stdout == ""
        assert result.returncode == 0

    @pytest.mark.parametrize(
        ("overrides", "expected_fragment"),
        [
            ({"plan_review_thread_id": ""}, "plan_review_thread_id must not be なし"),
            ({"plan_review_history": ""}, "plan_review_history must not be なし"),
            ({"status": ""}, "status must be completed, completed_with_review_cap, or needs_escalation"),
            ({"plan_review_route": ""}, "plan_review_route must be codex or claude"),
        ],
    )
    def test_empty_scalar_value_does_not_consume_next_label(
        self,
        tmp_path: Path,
        overrides: dict[str, str],
        expected_fragment: str,
    ) -> None:
        """空欄を次ラベルの値として扱わず、各欄の値矛盾をblockする。"""
        label = next(iter(overrides))
        sid = f"sid-format-empty-{label}"
        agent_id = f"sub-empty-{label}"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(**overrides),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert expected_fragment in body["reason"]

    @pytest.mark.parametrize(
        ("status", "extra_fields"),
        [
            ("completed", {}),
            (
                "needs_escalation",
                {
                    "review_status": "レビュー未完了",
                    "plan_review_route": "unavailable",
                    "plan_review_thread_id": "なし",
                    "blockers": "- 未解決事項",
                },
            ),
            (
                "completed_with_review_cap",
                {
                    "review_status": "上限到達後の既知指摘修正済み（再レビューなし）",
                    "review_rounds": "5",
                },
            ),
        ],
    )
    def test_allowed_status_values_pass(
        self,
        tmp_path: Path,
        status: str,
        extra_fields: dict[str, str],
    ) -> None:
        """許可された3つのstatus値は整合する報告で通過する。"""
        sid = f"sid-format-allowed-status-{status}"
        agent_id = f"sub-allowed-status-{status}"
        _write_flag_state(tmp_path, sid, agent_id)
        report = _complete_report(status=status, **extra_fields)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": report,
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        assert result.stdout == ""

    @pytest.mark.parametrize("invalid_status", ["done", ""])
    def test_invalid_or_empty_status_blocks(self, tmp_path: Path, invalid_status: str) -> None:
        """許可集合外または空のstatusをblockする。"""
        suffix = invalid_status or "empty"
        sid = f"sid-format-invalid-status-{suffix}"
        agent_id = f"sub-invalid-status-{suffix}"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(status=invalid_status),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert "status must be completed, completed_with_review_cap, or needs_escalation" in body["reason"]

    @pytest.mark.parametrize(
        ("overrides", "expected_fragment"),
        [
            ({"review_rounds": "1"}, "review_rounds must be 0"),
            ({"plan_review_route": "codex"}, "plan_review_route must be not_started"),
            ({"independent_review_thread_id": "th_invalid"}, "independent_review_thread_id must be なし"),
            ({"plan_review_history": "指摘なし"}, "plan_review_history must be なし"),
            ({"review_resolution": "指摘なし"}, "review_resolution must be なし"),
            ({"review_final_findings": "計画準拠系0件・独立系0件"}, "review_final_findings must be 対象外"),
            ({"review_skip_instruction": "なし"}, "review_skip_instruction must preserve the user instruction"),
            ({"review_caller_verification": "不要"}, "review_caller_verification must request user instruction"),
            ({"review_coverage": "点検済み"}, "review_coverage must be なし"),
            ({"review_impact_audit": "指摘なし"}, "review_impact_audit must be なし"),
        ],
    )
    def test_skipped_review_value_mismatch_blocks(
        self,
        tmp_path: Path,
        overrides: dict[str, str],
        expected_fragment: str,
    ) -> None:
        """レビュー省略時の値矛盾をblockする。"""
        sid = f"sid-format-skip-mismatch-{len(expected_fragment)}-{len(overrides)}"
        agent_id = f"sub-skip-mismatch-{len(expected_fragment)}-{len(overrides)}"
        _write_flag_state(tmp_path, sid, agent_id)
        skipped = {
            "review_status": "レビューは実施しない（ユーザー指示）",
            "plan_review_thread_id": "なし",
            "independent_review_thread_id": "なし",
            "plan_review_route": "not_started",
            "independent_review_route": "not_started",
            "review_rounds": "0",
            "plan_review_history": "なし",
            "independent_review_history": "なし",
            "review_resolution": "なし",
        }
        skipped.update(overrides)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(**skipped),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        body = json.loads(result.stdout)
        assert body["decision"] == "block"
        assert expected_fragment in body["reason"]

    def test_skipped_review_consistent_values_pass(self, tmp_path: Path) -> None:
        """レビュー省略時の整合した値は通過する。"""
        sid = "sid-format-skip-ok"
        agent_id = "sub-skip-ok"
        _write_flag_state(tmp_path, sid, agent_id)
        result = _run_with_state_dir(
            {
                "session_id": sid,
                "last_assistant_message": _complete_report(
                    review_status="レビューは実施しない（ユーザー指示）",
                    plan_review_thread_id="なし",
                    independent_review_thread_id="なし",
                    plan_review_route="not_started",
                    independent_review_route="not_started",
                    review_rounds="0",
                    plan_review_history="なし",
                    independent_review_history="なし",
                    review_resolution="なし",
                ),
                "transcript_path": _transcript_path_for(tmp_path, agent_id),
            },
            tmp_path,
        )
        assert result.stdout == ""

    @pytest.mark.parametrize(
        ("overrides", "expected_fragment"),
        [
            ({"review_status": "実施完了（計画準拠系採用0件・独立系採用0件）"}, "レビュー未完了"),
            (
                {"plan_review_route": "unavailable", "plan_review_thread_id": "th_invalid"},
                "plan_review_thread_id must be なし",
            ),
            ({"review_final_findings": "対象外"}, "review_final_findings must be 未確定"),
            ({"review_skip_instruction": "省略すること"}, "review_skip_instruction must be なし"),
            ({"review_caller_verification": "不要"}, "review_caller_verification must request pending-item"),
        ],
    )
    def test_escalation_review_value_mismatch_blocks(
        self,
        tmp_path: Path,
        overrides: dict[str, str],
        expected_fragment: str,
    ) -> None:
        """エスカレーション時のreview statusと利用不能threadの矛盾をblockする。"""
        sid = f"sid-format-escalation-mismatch-{len(expected_fragment)}"
        agent_id = f"sub-escalation-mismatch-{len(expected_fragment)}"
        _write_flag_state(tmp_path, sid, agent_id)
        escalation = {
            "status": "needs_escalation",
            "review_status": "レビュー未完了",
            "plan_review_route": "unavailable",
            "plan_review_thread_id": "なし",
            "independent_review_route": "not_started",
            "independent_review_thread_id": "なし",
        }
        escalation.update(overrides)
        report = _complete_report(**escalation) + "\nblockers:\n- 未解決事項"
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
        assert expected_fragment in body["reason"]

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
        report = _complete_report(status="needs_escalation")
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
        report = (
            _complete_report(
                status="needs_escalation",
                review_status="レビュー未完了",
                plan_review_route="unavailable",
                plan_review_thread_id="なし",
            )
            + "\nblockers:\n- 未解決事項"
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

    def test_flag_entry_removed_after_check(self, tmp_path: Path) -> None:
        """SubagentStop発火時に該当agentIdのエントリのみを状態辞書から削除し、他の並行エントリは保持する。"""
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
        assert "sub-e" not in active
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
            blockers="- [ ] 未解決の論点",
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
