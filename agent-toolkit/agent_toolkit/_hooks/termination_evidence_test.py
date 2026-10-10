"""可視発話と準備結果・判断記録が同じ作業のStop判定へ届くことを確かめる。"""

import argparse
import contextlib
import io
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import shared_layout
from agent_toolkit._atk import run_script
from agent_toolkit._common import session_state, state_paths
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._hooks import (
    agents_server_observations,
    posttooluse,
    pretooluse,
    termination_evidence,
    termination_order_advisor,
    user_prompt_submit,
)
from agent_toolkit._testing import delegated_threads

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
                {
                    "uuid": call_id,
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
                },
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


def _supply_prepare(
    directory: pathlib.Path,
    call_id: str,
    *,
    candidates: list[str] | None = None,
    improvements: list[str] | None = None,
    failed: bool = False,
) -> dict[str, Any]:
    """実際の準備応答の形を、公開hook入力で同じ作業へ供給する。"""
    outputs = {}
    for key in ("conversation_path", "candidates_path", "stats_path"):
        path = directory / f"{call_id}-{key}.md"
        path.write_text("準備した入力", encoding="utf-8")
        outputs[key] = str(path)
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": call_id,
        "transcript_path": str(directory / "transcript.jsonl"),
        "tool_input": {"command": "atk run-script session-review-prepare -- --transcript /record --work-dir /work"},
    }
    termination_evidence.observe_tool(json.dumps(payload), after=False)
    payload["tool_response"] = {
        "stdout": json.dumps(
            {
                **outputs,
                "prepared_at": "2026-10-10T00:00:00Z",
                "mandatory_candidates": candidates or [],
                "improvement_lines": improvements or [],
            }
        ),
        "exit_code": 1 if failed else 0,
    }
    termination_evidence.observe_tool(json.dumps(payload), after=True)
    return payload["tool_response"]


def _result_for(candidate: str) -> str:
    return (
        "## 振り返り結果報告\n\n### 対策を見送った問題\n\n"
        f"- 判定済み: 状態の照会; 根拠: single-inquiry: 状態だけを尋ねた（{candidate}）\n"
    )


@pytest.mark.parametrize("old_heading", ["振り返り結果の予告", "振り返り結果報告", "AWI投入結果報告"])
def test_reports_after_latest_prepare(tmp_path: pathlib.Path, old_heading: str) -> None:
    """新しい成功準備より前の報告を、再取込して最新候補の不足へ使わない。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    _supply_prepare(tmp_path, "first", candidates=["c0001"])
    old = _result_for("c0001").replace("振り返り結果報告", old_heading)
    supply_report(tmp_path, old, "review-result", "old")
    _supply_prepare(tmp_path, "second", candidates=["c0002"])
    supply_report(tmp_path, _result_for("c0002"), "review-result", "new")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, _result_for("c0002")))
    assert decision == "approve", reason
    work = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "")))[0][1]
    assert "work-complete" in work["reports"]
    assert "review-preview" not in work["reports"] and "review-submission" not in work["reports"]


def test_latest_report_and_submission_requirements(tmp_path: pathlib.Path) -> None:
    """最新の不足は残し、古い投入結果で新しい予告の後工程を充足しない。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    _supply_prepare(tmp_path, "first")
    supply_report(tmp_path, "## AWI投入結果報告\n\n投入した。", "review-submission", "old-submission")
    _supply_prepare(tmp_path, "second", candidates=["c0002"])
    preview = "## 振り返り結果の予告\n\n### 確定した問題と対策\n\n- AWI登録予定: 新しい対策（c0002）\n"
    supply_report(tmp_path, preview, "review-preview", "new-preview")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, preview))
    assert decision == "block" and "review-submission" in reason
    supply_report(tmp_path, _result_for("c0009"), "review-result", "missing")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, _result_for("c0009")))
    assert decision == "block" and "c0002" in reason


def test_failed_prepare_keeps_successful_report_boundary(tmp_path: pathlib.Path) -> None:
    """再準備の失敗は、成功した準備と報告・作業完了の対応を失わせない。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    _supply_prepare(tmp_path, "first", candidates=["c0001"])
    supply_report(tmp_path, _result_for("c0001"), "review-result", "result")
    _supply_prepare(tmp_path, "failed", candidates=["c0002"], failed=True)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, _result_for("c0001")))
    assert decision == "approve", reason
    work = termination_evidence.pending_work(json.loads(stop_payload(tmp_path, "")))[0][1]
    assert work["prepare"][-1]["result"]["mandatory_candidates"] == ["c0001"]
    assert "work-complete" in work["reports"]


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("delivery", ["text", "send", "codex"])
def test_unprepared_and_late_improvements(tmp_path: pathlib.Path, delivery: str, *, prepared: bool) -> None:
    """未準備と最終報告後の新しい行を、可視本文・送信・Codexの同じStop入力から回収する。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    if prepared:
        _supply_prepare(tmp_path, "first")
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "result")
    improvement = "気付いた改善点: 同じ資料を何度も読む作業がある"
    if delivery == "send":
        entry = {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "mcp__agent-toolkit__send_to_user", "input": {"message": improvement}}]
            },
        }
    elif delivery == "codex":
        entry = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "channel": "final",
                "content": [{"type": "output_text", "text": improvement}],
            },
        }
    else:
        entry = {"type": "assistant", "message": {"content": [{"type": "text", "text": improvement}]}}
    with (tmp_path / "transcript.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, improvement))
    assert decision == "block" and improvement in reason and "工程1" in reason
    _supply_prepare(tmp_path, "second", improvements=[improvement])
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "new-result")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, improvement))[0] == "approve"


def test_prepare_failure_allows_evidenced_blocked_decision(tmp_path: pathlib.Path) -> None:
    """新しい改善点の再準備が不成立なら、実際の失敗応答で既存の技術的不成立の判断へ進める。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    _supply_prepare(tmp_path, "first")
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "result")
    improvement = "気付いた改善点: 追加で分析する行"
    supply_report(tmp_path, improvement, "review-result", "late")
    response = _supply_prepare(tmp_path, "failed", failed=True)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, improvement))[0] == "block"
    termination_evidence.record_decision(
        {
            "session_id": "evidence-test",
            "action": "blocked",
            "work_id": "work-1",
            "call_id": "failed",
            "quote": json.dumps(response, ensure_ascii=False, sort_keys=True),
            "reason": "再準備を実行できない",
        }
    )
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, improvement))[0] == "approve"


def test_real_prepare_and_stop_share_consumed_improvements(tmp_path: pathlib.Path) -> None:
    """同じ記録を実際の準備CLIとStopへ渡し、取込み後の再掲と引用だけでは循環しない。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    transcript = tmp_path / "transcript.jsonl"
    improvement = "気付いた改善点: この記録で準備とStopを対照する"
    for index in range(2):
        directory = tmp_path / f"prepare-{index}"
        directory.mkdir()
        arguments = ["--transcript", str(transcript), "--work-dir", str(directory)]
        payload: dict[str, Any] = {
            "session_id": "evidence-test",
            "tool_name": "Bash",
            "tool_use_id": f"prepare-{index}",
            "transcript_path": str(transcript),
            "tool_input": {
                "command": f"atk run-script session-review-prepare -- --transcript {transcript} --work-dir {directory}"
            },
        }
        termination_evidence.observe_tool(json.dumps(payload), after=False)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert run_script.dispatch(argparse.Namespace(script_name="session-review-prepare", script_args=arguments)) == 0
        result = json.loads(output.getvalue())
        assert result["improvement_lines"] == ([] if index == 0 else [improvement])
        payload["tool_response"] = {"stdout": output.getvalue(), "exit_code": 0}
        termination_evidence.observe_tool(json.dumps(payload), after=True)
        supply_report(tmp_path, REVIEW_RESULT, "review-result", f"result-{index}")
        if index == 0:
            supply_report(tmp_path, improvement, "review-result", "late")
            assert termination_order_advisor.evaluate(stop_payload(tmp_path, improvement))[0] == "block"
    copied = improvement + "\n```text\n気付いた改善点: 文書の例\n```"
    supply_report(tmp_path, copied, "review-result", "copy")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, copied))[0] == "approve"


@pytest.mark.parametrize("delivery", ["text", "send", "codex"])
def test_late_delegate_improvements(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, delivery: str) -> None:
    """準備後の正常返却をメインが中継し、実準備に含めてから同じStopで終了する。"""
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(threads.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    transcript = tmp_path / "transcript.jsonl"
    parent_entries = threads.transcript.read_text(encoding="utf-8").splitlines()
    assert json.loads(parent_entries[-1])["message"]["content"] == "終了"
    # 費用計測用fixtureの追加ユーザー発話は、この正常返却のシナリオには含めない。
    transcript.write_text("\n".join(parent_entries[:-1]) + "\n", encoding="utf-8")
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    _supply_prepare(tmp_path, "first")
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "result")
    improvement = "気付いた改善点: 正常な委譲返却で追加された機会"
    body = "実装完了\n" + "説明。" * 1000 + "\n" + improvement + "\n" + "補足。" * 1000
    delegated_threads.append_return(threads, body, delivery)
    # 元の返却と、メインが既存の中継契約で届ける実際の報告を区別する。
    returned = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "wait", "content": body}]},
    }
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(returned, ensure_ascii=False) + "\n")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, ""))[0] == "approve"
    supply_report(tmp_path, body, "review-result", "relay")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, ""))
    assert decision == "block" and improvement in reason and "工程1" in reason
    directory = tmp_path / "second-prepare"
    directory.mkdir()
    arguments = ["--transcript", str(transcript), "--work-dir", str(directory)]
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": "second",
        "transcript_path": str(transcript),
        "tool_input": {"command": f"atk run-script session-review-prepare -- --transcript {transcript} --work-dir {directory}"},
    }
    termination_evidence.observe_tool(json.dumps(payload), after=False)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert run_script.dispatch(argparse.Namespace(script_name="session-review-prepare", script_args=arguments)) == 0
    result = json.loads(output.getvalue())
    assert result["improvement_lines"] == [improvement]
    assert improvement in pathlib.Path(result["candidates_path"]).read_text(encoding="utf-8")
    payload["tool_response"] = {"stdout": output.getvalue(), "exit_code": 0}
    termination_evidence.observe_tool(json.dumps(payload), after=True)
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "updated-result")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, ""))
    assert decision == "approve", reason


def test_consumed_improvements_and_quotes(tmp_path: pathlib.Path) -> None:
    """準備済みの再掲と、文書・コード・生成通知の引用を新しい改善点へ数えない。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    improvement = "気付いた改善点: 既に準備へ含めた行"
    _supply_prepare(tmp_path, "first", improvements=[improvement])
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "result")
    text = (
        f"{improvement}\n```text\n気付いた改善点: コードの例\n```\n"
        "> 気付いた改善点: 引用の例\n"
        '<atk-auto source="notice" kind="notice">\n気付いた改善点: 生成通知の例\n</atk-auto>'
    )
    supply_report(tmp_path, text, "review-result", "copies")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, text))[0] == "approve"


def test_first_stop_requires_following_visible_result(tmp_path: pathlib.Path) -> None:
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "作業を終えた。"))
    assert decision == "block" and "review-result" in reason
    assert "completion-report-check" not in reason
    supply_report(tmp_path, REVIEW_RESULT, "review-result", "call-2")
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "終了する。"))[0] == "approve"


def test_send_to_user_message_delivers_report(tmp_path: pathlib.Path) -> None:
    """send_to_userの呼び出しだけの応答も、その`message`を可視本文として報告段階へ数える。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "call-1")
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__agent-toolkit__send_to_user",
                    "input": {"message": REVIEW_RESULT},
                }
            ]
        },
    }
    with (tmp_path / "transcript.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    assert REVIEW_RESULT in (termination_evidence.visible_messages(json.loads(stop_payload(tmp_path, "")), 0) or [])
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


def test_plugin_origin_is_not_human_termination_input(tmp_path: pathlib.Path) -> None:
    """同形のコマンドを生成元だけで分け、人間の入力と回答を判断の根拠へ残す。"""
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
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


_MANDATORY_REPORTS = {
    "id-missing": (
        "### 対策を見送った問題\n\n- 判定済み: 技術判断の確認; 根拠: 原因分析の結果、既存の規範の当てはめ誤りと判断した\n",
        "block",
    ),
    "id-only": (
        "### 対策を見送った問題\n\n- 判定済み: 技術判断の確認; 根拠: 既存の規範の当てはめ誤りと判断した（c0006）\n",
        "block",
    ),
    "repeat-without-record": (
        "### 対策を見送った問題\n\n"
        "- 再発防止策なし: 技術判断の確認; 評価した案: 条文の追記; 採らない理由: 恒常コスト; 反復: 一致なし（c0006）\n",
        "block",
    ),
    "measure": ("### 確定した問題と対策\n\n- AWI登録予定: 確認要否の判定へ目的との比較を置く（c0006）\n", "approve"),
    "exclusion": (
        "### 対策を見送った問題\n\n- 判定済み: 技術判断の確認; 根拠: non-changing-request: 要求を変えない回答だった（c0006）\n",
        "approve",
    ),
    "repeat-with-record": (
        "### 対策を見送った問題\n\n- 再発防止策なし: 技術判断の確認; 評価した案: 条文の追記; 採らない理由: 恒常コスト; "
        "反復: 20261001-163353-001.mdの対策は場面ごとの例外で、新しい場面で働かなかった（c0006）\n",
        "approve",
    ),
}


@pytest.mark.parametrize("case", list(_MANDATORY_REPORTS))
@pytest.mark.parametrize("heading", ["振り返り結果報告", "振り返り結果の予告"])
def test_mandatory_candidate_requires_id_or_allowed_skip(tmp_path: pathlib.Path, case: str, heading: str) -> None:
    """必須の候補を持つ準備結果では、候補IDの無い報告と許されない見送りを遮断し、確定した対策と許される見送りを通す。

    CodexのStopも同じ`termination_order_advisor`を実行するため、ホストごとの差はこの判定に無い。
    """
    supply_report(tmp_path, WORK_COMPLETE, "work-complete", "complete")
    outputs = {}
    for key in ("conversation_path", "candidates_path", "stats_path"):
        outputs[key] = str(tmp_path / f"{key}.md")
        pathlib.Path(outputs[key]).write_text("記録", encoding="utf-8")
    prepared = {
        **outputs,
        "prepared_at": "2026-10-07T00:44:00Z",
        "mandatory_candidates": ["c0006"],
        "similar_records": {"c0006": ["20261001-163353-001.md", "20261005-103502-001.md"]},
    }
    payload = {
        "session_id": "evidence-test",
        "tool_name": "Bash",
        "tool_use_id": "prepare",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "tool_input": {"command": "atk run-script session-review-prepare -- --session current"},
    }
    with contextlib.redirect_stdout(io.StringIO()):
        pretooluse.main(json.dumps(payload))
    payload["tool_response"] = {"stdout": json.dumps(prepared), "stderr": ""}
    with contextlib.redirect_stdout(io.StringIO()):
        posttooluse.main(json.dumps(payload))
    body, expected = _MANDATORY_REPORTS[case]
    report = f"## {heading}\n\n" + body
    if "AWI登録予定" in body or heading == "振り返り結果の予告":
        report += "\n## AWI投入結果報告\n\n確定した対策を投入した。\n\n### 投入したAWI\n\n- 20261007-102936-001.md: 対策\n"
    supply_report(tmp_path, report, "review-result", "review")
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, report))
    assert decision == expected, reason
    if expected == "block":
        assert "c0006" in reason


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
        ("確定した問題と対策", "- 問題: 対策", "- 実装済み: 対策; 根拠: 変更commit"),
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
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
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
    root = shared_layout.status_directory("evidence-test", tmp_path / "state")
    lock_path = root / "wait-locks" / "root.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    targets = shared_layout.wait_targets_directory("evidence-test", "root.json", tmp_path / "state")
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
            assert agents_server_observations.actively_waited_session_ids(["child-test"]) == {"child-test"}
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
