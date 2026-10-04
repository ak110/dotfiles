"""可視発話と準備結果・判断記録が同じ作業のStop判定へ届くことを確かめる。"""

import argparse
import contextlib
import io
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import status_file
from agent_toolkit._atk import config, run_script
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._hooks import (
    agents_server_session_advisor,
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


def supply_report(directory: pathlib.Path, text: str, stage: str, call_id: str) -> None:
    """報告をClaude形式の可視発話として記録し、初回Stopから取り込む。"""
    del stage
    transcript = directory / "transcript.jsonl"
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {"uuid": call_id, "type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
                ensure_ascii=False,
            )
            + "\n"
        )
    termination_order_advisor.evaluate(stop_payload(directory, ""))


def stop_payload(directory: pathlib.Path, visible: str) -> str:
    return json.dumps(
        {
            "session_id": "evidence-test",
            "transcript_path": str(directory / "transcript.jsonl"),
            "stop_hook_active": False,
            "last_assistant_message": visible,
        }
    )


def test_first_stop_requires_following_visible_result(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "作業を終えた。"))
    assert decision == "block" and "review-result" in reason
    assert "completion-report-check" not in reason
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "call-2")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "終了する。"))[0] == "approve"


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
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": "human",
                    "prompt": "この作業を中止し、次の作業を始める",
                    "transcript_path": str(tmp_path / "transcript.jsonl"),
                }
            )
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
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "block"


def test_cancelling_current_work_keeps_remaining_stage_of_other_work(tmp_path: pathlib.Path) -> None:
    """後から始めた作業の中止は、先の作業に残る振り返り結果報告の不足を判定の対象に残す。

    中止を報告の取得不能と同じに扱うと、先の作業の不足を判定せずにターンが終わり、振り返りの報告が欠ける。
    """
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    prompt = "別の作業を始めたが、それは中止する"
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": "switch",
                    "prompt": prompt,
                    "transcript_path": str(tmp_path / "transcript.jsonl"),
                }
            )
        )
    document = {"session_id": "evidence-test", "input_id": "switch", "quote": prompt, "reason": "後の作業の中止"}
    later_id = termination_evidence.record_decision({**document, "action": "start"})
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": later_id})
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "中止した。"))
    assert decision == "block"
    assert "作業 work-1: review-result" in reason
    assert later_id not in reason


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
    with pytest.raises(ValueError, match="作業に対応しません"):
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
                "content": [{"type": "output_text", "text": REVIEW_RESULT}],
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
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": REVIEW_RESULT}]}}
        if kind in {"thinking", "tool"}:
            event["message"]["content"][0]["type"] = "thinking" if kind == "thinking" else "tool_result"
        elif kind == "user":
            event["type"] = "user"
        else:
            event["isSidechain"] = True
    (tmp_path / "transcript.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "block"


def test_corrupt_or_missing_observation_does_not_repeat_block(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    (tmp_path / "transcript.jsonl").write_text("{\n", encoding="utf-8")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "approve"
    (tmp_path / "transcript.jsonl").unlink()
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "登録した。"))[0] == "approve"


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
        user_prompt_submit.main(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": "next",
                    "prompt": "次の作業を頼む",
                    "transcript_path": str(tmp_path / "transcript.jsonl"),
                }
            )
        )
    supply_report(tmp_path, WORK_COMPLETE.replace("変更した", "次を変更した"), "work-complete", "call-3")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))
    assert decision == "block" and ": review-result" in reason and "work-1" not in reason


@pytest.mark.parametrize(
    ("section", "invalid_item", "corrected_item"),
    [
        ("対策を見送った問題", "- 判定済み: 問題", "- 判定済み: 問題; 根拠: 既存操作で解決できる"),
        ("確定した問題と対策", "- 問題: 対策", "- 問題: 対策（同一セッションで実装済み: 変更commit）"),
    ],
)
def test_invalid_report_can_be_corrected_after_new_input(
    tmp_path: pathlib.Path, section: str, invalid_item: str, corrected_item: str
) -> None:
    """本文違反が残る作業には、後続入力後の訂正も同じ作業へ取り込む。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    invalid = f"## 振り返り結果報告\n\n### {section}\n\n{invalid_item}\n"
    supply_report(tmp_path, invalid, "review-result", "call-2")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, invalid))
    assert decision == "block" and ("根拠" in reason or "対策" in reason)
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": "correction",
                    "prompt": "振り返り報告を訂正する",
                    "transcript_path": str(tmp_path / "transcript.jsonl"),
                }
            )
        )
    corrected = invalid.replace(invalid_item, corrected_item)
    with (tmp_path / "transcript.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": corrected}]}}, ensure_ascii=False)
            + "\n"
        )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, corrected))[0] == "approve"
    state = session_state.read_state("evidence-test")[termination_evidence.STATE_KEY]
    assert len(state["works"]) == 1


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


def test_old_cli_accepted_body_does_not_replace_visible_report(tmp_path: pathlib.Path) -> None:
    """旧確認コマンドの受理本文が保存されていても未発話の段階へ使わない。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")

    def add_old_receipt(state: dict) -> dict:
        state[termination_evidence.STATE_KEY]["works"]["work-1"]["reports"]["review-result"] = {
            "text": REVIEW_RESULT,
            "call_id": "old-check",
            "delivered": True,
        }
        return state

    session_state.update_state("evidence-test", add_old_receipt)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "終了する。"))
    assert decision == "block" and "review-result" in reason


def test_public_wait_decision_uses_cli_lock_after_observation_attempt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """委譲開始とPostToolUseの観測更新を経て、CLIの待機所有権で公開wait判断を受理する。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path / "state")
    start = {
        "session_id": "evidence-test",
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_use_id": "start-test",
        "tool_input": {"prompt": "独立した調査"},
        "tool_response": {"structuredContent": {"session_id": "child-test", "status": "running"}},
    }
    termination_evidence.observe_tool(json.dumps(start), after=False)
    with contextlib.redirect_stdout(io.StringIO()):
        assert posttooluse.main(json.dumps(start)) == 0
    wait = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": "wait-test",
        "tool_input": {"command": "atk agents wait", "run_in_background": True},
        "tool_response": {"stdout": "継続中の待機", "stderr": "", "exit_code": 0},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        assert posttooluse.main(json.dumps(wait)) == 0
    child = session_state.read_state("evidence-test")["agents_server_sessions"]["child-test"]
    assert child["pending_observation"] is False
    root = status_file.status_directory("evidence-test", tmp_path / "state")
    lock_path = root / "wait-locks" / "root.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    targets = status_file.wait_targets_directory("evidence-test", "root.json", tmp_path / "state")
    targets.mkdir(parents=True, exist_ok=True)
    (targets / "child-test.json").write_text('{"version":1,"session_id":"child-test"}', encoding="utf-8")
    document = tmp_path / "wait-decision.json"
    document.write_text(
        json.dumps(
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "child-test",
                "reason": "調査結果を待つ",
            }
        ),
        encoding="utf-8",
    )
    with lock_path.open("a+b") as stream:
        acquire_lock(stream, blocking=False)
        try:
            assert agents_server_session_advisor.actively_waited_session_ids(["child-test"]) == {"child-test"}
            with contextlib.redirect_stdout(io.StringIO()):
                assert (
                    run_script.dispatch(
                        argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(document)])
                    )
                    == 0
                )
            assert not termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "待機中")))
        finally:
            release_lock(stream)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "結果を受け取った。"))
    assert decision == "block" and "review-result" in reason


@pytest.mark.parametrize("kind", ["unrelated", "other-owner", "finished"])
def test_explicit_child_wait_rejection_is_not_uwi_guidance(tmp_path: pathlib.Path, kind: str) -> None:
    """作業不一致・所有者違い・待機解消を対象ごとの理由で拒否する。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")

    def register(state: dict) -> dict:
        work = state[termination_evidence.STATE_KEY]["works"]["work-1"]
        if kind != "unrelated":
            work["async_targets"]["child-x"] = "call-x"
        state["agents_server_sessions"] = {
            "child-x": {"owner_agent_id": "another" if kind == "other-owner" else "main", "pending_observation": False}
        }
        return state

    session_state.update_state("evidence-test", register)
    with pytest.raises(ValueError) as error:
        termination_evidence.record_decision(
            {
                "session_id": "evidence-test",
                "action": "wait",
                "work_id": "work-1",
                "target_session_id": "child-x",
                "reason": "対象の結果を待つ",
            }
        )
    assert "child-x" in str(error.value) and "UWI" not in str(error.value)
