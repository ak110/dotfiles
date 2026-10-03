"""公開の報告構造確認の実出力が供給イベントから初回Stopへ届くことを確かめる。"""

import argparse
import contextlib
import io
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._hooks import (
    completion_report_delivery_advisor,
    posttooluse,
    pretooluse,
    session_state,
    termination_evidence,
    termination_order_advisor,
    user_prompt_submit,
)

WORK_COMPLETE = "## 作業完了報告\n\n完了した。\n\n### 成果\n\n- 変更した\n\n### 投入したWI\n\n- なし\n"
REVIEW_RESULT = "## 振り返り結果報告\n\n### 振り返り\n\n- session-review未実施: 成果を再利用したため起動省略\n"


@pytest.fixture(autouse=True)
def isolated_session(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """実セッションの状態と委譲印を試験へ混ぜない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))


def supply_report(
    directory: pathlib.Path,
    text: str,
    stage: str,
    call_id: str,
    *,
    before_only: bool = False,
) -> dict:
    """登録済みの報告構造確認コマンドを実行し、その両出力と終了状態をPostへ渡す。"""
    report = directory / "report.md"
    report.write_text(text, encoding="utf-8")
    transcript = directory / "transcript.jsonl"
    transcript.touch(exist_ok=True)
    arguments = [str(report), "--stage", stage]
    if stage == "review-result":
        arguments += ["--review-state", "not-run"]
    payload = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": call_id,
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "tool_input": {"command": "atk run-script completion-report-check -- " + " ".join(arguments)},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        assert pretooluse.main(json.dumps(payload)) == 0
    if before_only:
        return payload
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = run_script.dispatch(argparse.Namespace(script_name="completion-report-check", script_args=arguments))
    payload["tool_response"] = {"stdout": stdout.getvalue(), "stderr": stderr.getvalue(), "exit_code": status}
    with contextlib.redirect_stdout(io.StringIO()):
        assert posttooluse.main(json.dumps(payload)) == 0
    return payload


def stop_payload(directory: pathlib.Path, visible: str) -> str:
    return json.dumps(
        {
            "session_id": "evidence-test",
            "transcript_path": str(directory / "transcript.jsonl"),
            "stop_hook_active": False,
            "last_assistant_message": visible,
        }
    )


def test_first_stop_requires_result_and_visible_report(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    payload = stop_payload(tmp_path, "登録した。")
    assert termination_order_advisor.evaluate(payload)[0] == "block"
    decision, reason = completion_report_delivery_advisor.evaluate(payload)
    assert decision == "block" and WORK_COMPLETE in reason
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "call-2")
    payload = stop_payload(tmp_path, WORK_COMPLETE + "\n" + REVIEW_RESULT)
    assert termination_order_advisor.evaluate(payload)[0] == "approve"
    assert completion_report_delivery_advisor.evaluate(payload)[0] == "approve"


def test_invocation_without_result_is_not_accepted(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1", before_only=True)
    works = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "")))
    assert len(works) == 1 and not works[0][1]["reports"]


def test_invalid_recheck_does_not_reuse_previous_result(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "call-2")
    supply_report(tmp_path, "構造がない本文", "review-result", "call-3")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE + REVIEW_RESULT))[0] == "block"


def test_same_body_keeps_visible_origin_but_changed_body_requires_new_speech(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": WORK_COMPLETE}]}}) + "\n",
        encoding="utf-8",
    )
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-2")
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "approve"
    supply_report(tmp_path, WORK_COMPLETE.replace("変更した", "変更して検証した"), "work-complete", "call-3")
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "block"


def test_generated_input_cannot_cancel_work(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
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


def test_cancel_and_new_start_do_not_share_same_path_results(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps({"session_id": "evidence-test", "turn_id": "human", "prompt": "この作業を中止し、次の作業を始める"})
        )
    document = {
        "session_id": "evidence-test",
        "input_id": "human",
        "quote": "この作業を中止し、次の作業を始める",
        "reason": "作業の切替",
    }
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": "work-1"})
    new_id = termination_evidence.record_decision({**document, "action": "start"})
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-2")
    works = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "")))
    assert [work_id for work_id, _ in works] == [new_id]
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "block"


def test_current_evidence_reclaims_old_attempts(tmp_path: pathlib.Path) -> None:
    for index in range(12):
        supply_report(tmp_path, WORK_COMPLETE, "work-complete", f"call-{index}")
    data = session_state.read_state("evidence-test")[termination_evidence.STATE_KEY]
    assert list(data["calls"]) == ["call-11"]


def test_late_response_cannot_replace_newer_failed_attempt(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    late = supply_report(tmp_path, REVIEW_RESULT, "review-result", "late", before_only=True)
    supply_report(tmp_path, "構造がない本文", "review-result", "newer")
    late["tool_response"] = {"stdout": REVIEW_RESULT, "stderr": "", "exit_code": 0}
    termination_evidence.observe_tool(json.dumps(late), after=True)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE + REVIEW_RESULT))[0] == "block"


def test_waiting_for_answer_is_scoped_and_returns_to_remaining_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    queue = tmp_path / "private-notes"
    inbox = queue / "inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(queue))
    question = inbox / "20261003-000000-001.md"
    original = "---\ntype: uwi\nsubmitter_session: evidence-test\n---\n\n## 確認\n\n目的を選んでください。\n\n## 回答\n"
    question.write_text(original, encoding="utf-8")
    termination_evidence.record_decision(
        {
            "session_id": "evidence-test",
            "action": "wait",
            "work_id": "work-1",
            "uwi_file": str(question),
            "quote": original,
            "reason": "選好の回答を待つ",
        }
    )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を待つ。"))[0] == "approve"
    question.write_text(original + "今日だけの残量を示す。\n", encoding="utf-8")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答を受け取った。"))[0] == "block"


def test_unrelated_or_invented_wait_target_does_not_clear_remaining_work(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    with pytest.raises(ValueError, match="未回答UWI"):
        termination_evidence.record_decision(
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "存在しない委譲先",
                "reason": "待機したという申告",
            }
        )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "block"


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("kind", ["thinking", "tool", "user", "sidechain"])
def test_nonvisible_or_other_actor_text_does_not_deliver_report(tmp_path: pathlib.Path, host: str, kind: str) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    if host == "codex":
        event: dict[str, Any] = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "channel": "final",
                "content": [{"type": "output_text", "text": WORK_COMPLETE}],
            },
        }
        if kind == "thinking":
            event["payload"]["channel"] = "analysis"
        elif kind == "tool":
            event["payload"]["type"] = "function_call_output"
        elif kind == "user":
            event["payload"]["role"] = "user"
        else:
            event["agent_id"] = "child"
    else:
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": WORK_COMPLETE}]}}
        if kind in {"thinking", "tool"}:
            event["message"]["content"][0]["type"] = "thinking" if kind == "thinking" else "tool_result"
        elif kind == "user":
            event["type"] = "user"
        else:
            event["isSidechain"] = True
    (tmp_path / "transcript.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "block"


def test_corrupt_or_missing_observation_does_not_repeat_block(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    (tmp_path / "transcript.jsonl").write_text("{\n", encoding="utf-8")
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "approve"
    (tmp_path / "transcript.jsonl").unlink()
    assert completion_report_delivery_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "approve"


def test_search_term_or_quoted_name_is_not_an_invocation(tmp_path: pathlib.Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.touch()
    for index, command in enumerate(
        [
            "rg -F 'atk run-script completion-report-check' docs",
            "echo atk run-script completion-report-check --stage work-complete",
        ]
    ):
        payload = {
            "session_id": "evidence-test",
            "tool_name": "Bash",
            "tool_use_id": f"search-{index}",
            "transcript_path": str(transcript),
            "tool_input": {"command": command},
            "tool_response": {"stdout": WORK_COMPLETE, "stderr": "", "interrupted": False},
        }
        with contextlib.redirect_stdout(io.StringIO()):
            pretooluse.main(json.dumps(payload))
            posttooluse.main(json.dumps(payload))
    assert termination_evidence.STATE_KEY not in session_state.read_state("evidence-test")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "検索した。"))[0] == "approve"


def test_failed_check_keeps_stage_missing(tmp_path: pathlib.Path) -> None:
    payload = supply_report(tmp_path, WORK_COMPLETE, "work-complete", "failed", before_only=True)
    payload["hook_event_name"] = "PostToolUseFailure"
    payload["tool_response"] = {"stdout": WORK_COMPLETE, "stderr": "構造の不足", "exit_code": 1}
    termination_evidence.observe_tool(json.dumps(payload), after=True)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))
    assert decision == "block" and "work-complete" in reason


def test_prepare_requires_following_result(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    outputs = {}
    for key in ("conversation_path", "candidates_path", "stats_path"):
        outputs[key] = str(tmp_path / f"{key}.md")
        pathlib.Path(outputs[key]).write_text("記録", encoding="utf-8")
    payload = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": "prepare",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "tool_input": {"command": "atk run-script session-review-prepare -- --session current"},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        pretooluse.main(json.dumps(payload))
    payload["tool_response"] = {"stdout": json.dumps({**outputs, "prepared_at": "2026-10-03T00:00:00"}), "stderr": ""}
    with contextlib.redirect_stdout(io.StringIO()):
        posttooluse.main(json.dumps(payload))
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))
    assert decision == "block" and "review-result" in reason


def test_finished_work_is_not_reused_after_new_input(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "call-2")
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(json.dumps({"session_id": "evidence-test", "turn_id": "next", "prompt": "次の作業を頼む"}))
    supply_report(tmp_path, WORK_COMPLETE.replace("変更した", "次を変更した"), "work-complete", "call-3")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))
    assert decision == "block" and ": review-result" in reason and "work-1" not in reason


def test_async_wait_is_scoped_to_real_child_and_returns_after_end(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    start = {
        "session_id": "evidence-test",
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_use_id": "start-1",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "tool_input": {"prompt": "調査する"},
    }
    termination_evidence.observe_tool(json.dumps(start), after=False)
    start["tool_response"] = {"structuredContent": {"session_id": "child-1"}}
    termination_evidence.observe_tool(json.dumps(start), after=True)

    def register(state: dict) -> dict:
        state.setdefault("agents_server_sessions", {})["child-1"] = {"owner_agent_id": "main", "pending_observation": True}
        return state

    session_state.update_state("evidence-test", register)
    termination_evidence.record_decision(
        {
            "session_id": "evidence-test",
            "action": "wait",
            "work_id": "work-1",
            "target_session_id": "child-1",
            "reason": "調査結果を待つ",
        }
    )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "approve"

    def finish(state: dict) -> dict:
        state["agents_server_sessions"]["child-1"]["pending_observation"] = False
        return state

    session_state.update_state("evidence-test", finish)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "結果を受け取った"))[0] == "block"


def test_blocked_requires_actual_failure_of_same_work(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    failure = {
        "session_id": "evidence-test",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "tool_use_id": "network",
        "tool_input": {"command": "uv sync"},
        "tool_response": {"stdout": "", "stderr": "接続できない", "exit_code": 1},
    }
    termination_evidence.observe_tool(json.dumps(failure), after=True)
    base = {"session_id": "evidence-test", "action": "blocked", "work_id": "work-1", "reason": "依存を取得できない"}
    with pytest.raises(ValueError, match="失敗応答"):
        termination_evidence.record_decision({**base, "call_id": "network", "quote": "成功した"})
    quote = json.dumps(failure["tool_response"], ensure_ascii=False, sort_keys=True)
    termination_evidence.record_decision({**base, "call_id": "network", "quote": quote})
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "取得できない"))[0] == "approve"


def test_decision_cli_rejects_completion_claim(tmp_path: pathlib.Path) -> None:
    decision = tmp_path / "decision.json"
    decision.write_text(
        json.dumps({"session_id": "evidence-test", "action": "complete", "work_id": "work-1", "reason": "完了した"}),
        encoding="utf-8",
    )
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        status = run_script.dispatch(
            argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(decision)])
        )
    assert status == 2


def test_notice_gives_identifiers_needed_for_decision(tmp_path: pathlib.Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(json.dumps({"session_id": "evidence-test", "turn_id": "human-1", "prompt": "作業を頼む"}))
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))[1]
    assert "evidence-test" in reason and "human-1" in reason and "review-result" in reason
