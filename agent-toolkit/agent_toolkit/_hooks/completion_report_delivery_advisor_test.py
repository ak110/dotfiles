"""受理本文の可視発話の判定と、自律モード・委譲先の除外を確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import completion_report_delivery_advisor, session_state, stop
from agent_toolkit._hooks.termination_evidence_test import (  # noqa: F401  # pylint: disable=unused-import
    WORK_COMPLETE,
    isolated_session,
    stop_payload,
    supply_report,
)


def test_unspoken_report_lists_required_body(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    decision, reason = completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "完了報告を登録した。"))
    assert decision == "block" and WORK_COMPLETE.strip() in reason


def test_abbreviated_body_is_not_delivery(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    abbreviated = WORK_COMPLETE.replace("\n### 投入したWI\n\n- なし\n", "")
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, abbreviated))[0] == "block"


def test_spoken_body_is_recorded_and_not_requested_again(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    spoken = WORK_COMPLETE.replace("\n", "\r\n") + "  "
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, spoken))[0] == "approve"
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "追加の質問へ答えた。"))[0] == "approve"


@pytest.mark.parametrize("flag", ["process_wi_skill_invoked", "autonomous_exit_invoked"])
def test_autonomous_mode_uses_uwi_acceptance_instead_of_speech(tmp_path: pathlib.Path, flag: str) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")

    def mark(state: dict) -> dict:
        state[flag] = True
        return state

    session_state.update_state("evidence-test", mark)
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "停止した。"))[0] == "approve"


def test_delegated_session_is_not_target(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    payload = json.loads(stop_payload(tmp_path, "返却した。"))
    payload["agent_id"] = "child-agent"
    assert completion_report_delivery_advisor.evaluate(json.dumps(payload))[0] == "approve"
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "返却した。"))[0] == "approve"


def test_codex_stop_returns_continuation_without_claude_fields(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    payload = json.loads(stop_payload(tmp_path, "登録した。"))
    payload["turn_id"] = "codex-turn"
    result = stop.evaluate(json.dumps(payload))
    assert set(result) == {"decision", "reason"} and result["decision"] == "block"
