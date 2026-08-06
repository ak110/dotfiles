"""SubagentStop advisorの簡素化後完了報告契約を検証する。"""

from __future__ import annotations

import json

import pytest
import subagent_stop_advisor as advisor


def _report(*, status: str = "completed", review_status: str = "completed", blockers: str = "- なし") -> str:
    return f"""status: {status}
summary: 完了
changed:
- 変更
external_operations:
- operation: なし
  target: なし
  result: not_applicable
  evidence: なし
verification:
- pytest: exit 0
plan_check:
- 計画ファイル: /tmp/plan.md
- 計画着手前SHA: {"a" * 40}
- 終了コード: 0
- 警告件数: 0
commit_sha: {"b" * 40}
review_status: {review_status}
review_rounds: 1
review_routes:
- 計画準拠系: codex/thread-plan
- 独立系: codex/thread-independent
review_targets:
- 計画準拠系: {"b" * 40}と計画
- 独立系: {"b" * 40}と公開契約
review_findings:
- 指摘なし
review_resolution:
- 指摘なし
pending_confirmations:
- なし
plan_gaps:
- なし
applied_instructions:
- なし
blockers:
{blockers}
"""


def _payload(report: str) -> dict[str, object]:
    return {
        "session_id": "session",
        "agent_id": "agent",
        "last_assistant_message": report,
    }


@pytest.fixture(autouse=True)
def _active_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        advisor,
        "read_state",
        lambda _session_id: {advisor._PLAN_IMPL_EXECUTOR_ACTIVE_KEY: {"agent": {}}},  # pylint: disable=protected-access
    )


def test_completed_report_passes() -> None:
    missing, background_violation, violations = advisor._inspect_plan_impl_executor_report_format(_payload(_report()))  # pylint: disable=protected-access
    assert missing == []
    assert not background_violation
    assert not violations


@pytest.mark.parametrize("label", advisor.PLAN_IMPL_EXECUTOR_REQUIRED_LABELS)
def test_each_required_label_is_enforced(label: str) -> None:
    report = _report().replace(f"{label}:", f"missing_{label}:", 1)
    missing, _background_violation, _violations = advisor._inspect_plan_impl_executor_report_format(_payload(report))  # pylint: disable=protected-access
    assert label in missing


def test_completed_requires_no_blockers() -> None:
    report = _report(blockers="- blocker_type: missing_input")
    _missing, _background, violations = advisor._inspect_plan_impl_executor_report_format(_payload(report))  # pylint: disable=protected-access
    assert any("blockers" in violation for violation in violations)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (f"commit_sha: {'b' * 40}", "commit_sha: なし", "commit_sha"),
        ("- pytest: exit 0", "- 未実施", "verification"),
        ("review_status: completed", "review_status: needs_escalation", "review"),
        ("- 指摘なし", "- 未解決指摘 P-1", "unresolved"),
    ],
)
def test_completed_rejects_incomplete_contract(old: str, new: str, expected: str) -> None:
    report = _report().replace(old, new, 1)
    _missing, _background, violations = advisor._inspect_plan_impl_executor_report_format(_payload(report))  # pylint: disable=protected-access
    assert any(expected in violation for violation in violations)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("review_rounds: 1", "review_rounds: 0", "review_rounds"),
        ("- 独立系: codex/thread-independent", "- 独立系:", "routes"),
        ("- 独立系: codex/thread-independent", "- 独立系: codex/thread-plan", "distinct"),
        (f"- 独立系: {'b' * 40}と公開契約", "- 独立系: HEADと公開契約", "targets"),
    ],
)
def test_completed_rejects_incomplete_review_evidence(old: str, new: str, expected: str) -> None:
    """二系統reviewの回数、経路分離、最終commit対象を必須にする。"""
    report = _report().replace(old, new, 1)
    _missing, _background, violations = advisor._inspect_plan_impl_executor_report_format(_payload(report))  # pylint: disable=protected-access
    assert any(expected in violation for violation in violations)


def test_completed_requires_resolution_for_each_finding() -> None:
    """実指摘IDをresolutionへ対応付けない完了報告を拒否する。"""
    report = _report().replace("- 指摘なし\nreview_resolution:\n- 指摘なし", "- P-1: 欠陥\nreview_resolution:\n- 指摘なし", 1)

    _missing, _background, violations = advisor._inspect_plan_impl_executor_report_format(_payload(report))  # pylint: disable=protected-access

    assert any("resolution" in violation for violation in violations)


def test_needs_escalation_allows_unexecuted_plan_check() -> None:
    report = _report(status="needs_escalation", review_status="needs_escalation", blockers="- blocker_type: missing_input")
    report = report.replace(
        f"- 計画ファイル: /tmp/plan.md\n- 計画着手前SHA: {'a' * 40}\n- 終了コード: 0\n- 警告件数: 0",
        "- 計画ファイル: 未実施\n- 計画着手前SHA: 未実施\n- 終了コード: 未実施\n- 警告件数: 未実施",
    )
    assert advisor._inspect_plan_check(report, status="needs_escalation", review_status="needs_escalation") is None  # pylint: disable=protected-access


def test_nonzero_plan_check_is_rejected() -> None:
    report = _report().replace("- 終了コード: 0", "- 終了コード: 1")
    violation = advisor._inspect_plan_check(report, status="completed", review_status="completed")  # pylint: disable=protected-access
    assert violation is not None


def test_recursive_hook_is_approved(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps({"stop_hook_active": True})) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "approve"
