# pylint: disable=unused-import,useless-import-alias  # pytestのautouse fixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""termination_evidence_workの契約テスト。"""

import contextlib
import io
import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import termination_evidence, termination_order_advisor, user_prompt_submit
from agent_toolkit._testing.termination_evidence_support import (
    _missing_completion,
    human_input,
    stop_payload,
    supply_commit,
    supply_completion,
)
from agent_toolkit._testing.termination_evidence_support import isolated_session as isolated_session

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


WORK_COMPLETE = "## 作業完了報告\n\n完了した。\n\n### 成果\n\n- 変更した\n\n### 投入したWI\n\n- なし\n"


COMPLETION_SKILL_TEXT = "---\nname: report-completion\nuser-invocable: false\n---\n\n# 作業完了報告とセッションの完了処理\n"


def test_new_input_keeps_work_identity_and_reuses_context_completion(tmp_path: pathlib.Path) -> None:
    """新しい依頼は別作業にし、本文が残る同じ文脈の起動証拠をStopが再利用する。"""
    supply_commit(tmp_path, "commit-1")
    supply_completion(tmp_path, "completion-1")
    human_input(tmp_path, "next", "次の作業を頼む")
    supply_commit(tmp_path, "commit-2")
    works = termination_evidence.session_works(json.loads(stop_payload(tmp_path, "")))
    assert len(works) == 2
    assert not works[1][1].get("completion_report")
    assert _missing_completion(tmp_path) == ["commit"]
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE)) == ("approve", "")


def test_completion_before_deliverable_in_same_work_is_evidence(tmp_path: pathlib.Path) -> None:
    """同じ作業の開始後に得た起動証拠は、その後の成果作業（振り返りの実装など）にも有効とする。"""
    human_input(tmp_path, "request", "作業を頼む")
    supply_completion(tmp_path)
    supply_commit(tmp_path)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")
    works = termination_evidence.session_works(json.loads(stop_payload(tmp_path, "")))
    assert all(not termination_evidence.missing_completion(work) for _, work in works)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")


@pytest.mark.parametrize("invoked,new_input,missing", [(False, False, True), (True, False, False), (True, True, False)])
def test_batch_report_save_reuses_invocation_in_same_context(tmp_path, invoked, new_input, missing):
    """batchの参照読取と起動を分け、次の作業も同じ文脈の起動証拠を再利用する。"""

    def observe(call_id, tool, inputs, response):
        payload = {
            "session_id": "evidence-test",
            "cwd": str(tmp_path),
            "tool_use_id": call_id,
            "tool_name": tool,
            "tool_input": inputs,
        }
        termination_evidence.observe_tool(json.dumps(payload), after=False)
        termination_evidence.observe_tool(json.dumps({**payload, "tool_response": response}), after=True)

    termination_evidence.observe_user(
        json.dumps({"session_id": "evidence-test", "turn_id": "batch", "prompt": "batchの報告を保存する"})
    )
    skill = tmp_path / "plugin/skills/report-completion/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(COMPLETION_SKILL_TEXT, encoding="utf-8")
    observe(
        "reference",
        "Bash",
        {"command": f"cat -- {skill}"},
        {"stdout": COMPLETION_SKILL_TEXT, "exit_code": 0},
    )
    if invoked:
        observe("completion", "Skill", {"skill": "agent-toolkit:report-completion"}, "起動")
    if new_input:
        termination_evidence.observe_user(
            json.dumps({"session_id": "evidence-test", "turn_id": "next", "prompt": "別の報告を保存する"})
        )
    observe(
        "report",
        "Bash",
        {"command": "atk wi add --type uwi --body-file report.md"},
        {"stdout": "成功: 1件を保存した", "exit_code": 0},
    )
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, ""))
    assert (decision == "notify") is missing
    if missing:
        assert "WIの保存" in reason


def test_generated_input_cannot_cancel_work(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
    prompt = '<atk-auto source="stop" kind="continuation">中止する</atk-auto>'
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(json.dumps({"session_id": "evidence-test", "turn_id": "generated", "prompt": prompt}))
    with pytest.raises(ValueError, match="生成入力"):
        termination_evidence.record_decision(
            {
                "session_id": "evidence-test",
                "action": "cancel",
                "work_id": "work-1",
                "input_id": "generated",
                "quote": prompt,
                "reason": "中止の解釈",
            }
        )


def test_plugin_origin_is_not_human_termination_input(tmp_path: pathlib.Path) -> None:
    """同形のコマンドを生成元だけで分け、人間の入力と回答を判断の根拠へ残す。"""
    supply_commit(tmp_path)
    for identity, origin in (("plugin", {"kind": "plugin"}), ("human", {"kind": "human"})):
        termination_evidence.observe_user(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": identity,
                    "prompt": "/compact",
                    "origin": origin,
                }
            )
        )
        state = session_state.read_state("evidence-test")[termination_evidence.STATE_KEY]
        assert state["inputs"][identity]["human"] is (identity == "human")


def test_cancel_and_new_start_do_not_share_same_path_results(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path, "commit-1")
    human_input(tmp_path, "human", "この作業を中止し、次の作業を始める")
    document = {
        "session_id": "evidence-test",
        "input_id": "human",
        "quote": "この作業を中止し、次の作業を始める",
        "reason": "作業の切替",
    }
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": "work-1"})
    new_id = termination_evidence.record_decision({**document, "action": "start"})
    supply_commit(tmp_path, "commit-2")
    works = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "")))
    assert [work_id for work_id, _ in works] == [new_id]
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "登録した。"))
    assert decision == "notify" and new_id in reason and "work-1:" not in reason


def test_cancelling_current_work_keeps_missing_completion_of_other_work(tmp_path: pathlib.Path) -> None:
    """後から始めた作業の中止は、先の作業に残る起動不足を判定の対象に残す。"""
    supply_commit(tmp_path)
    prompt = "別の作業を始めたが、それは中止する"
    human_input(tmp_path, "switch", prompt)
    document = {"session_id": "evidence-test", "input_id": "switch", "quote": prompt, "reason": "後の作業の中止"}
    later_id = termination_evidence.record_decision({**document, "action": "start"})
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": later_id})
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "中止した。"))
    assert decision == "notify"
    assert "作業 work-1: commit" in reason
    assert later_id not in reason
