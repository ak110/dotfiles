"""成果作業・起動証拠・判断記録が同じ作業のStop判定へ届くことを確かめる。"""

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

WORK_COMPLETE = "## 作業完了報告\n\n完了した。\n\n### 成果\n\n- 変更した\n\n### 投入したWI\n\n- なし\n"
COMPLETION_SKILL_TEXT = "---\nname: completion-report\nuser-invocable: false\n---\n\n# 作業完了報告とセッションの完了処理\n"


@pytest.fixture(autouse=True)
def isolated_session(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """実セッションの状態と委譲印を試験へ混ぜない。"""
    for key in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    (tmp_path / "transcript.jsonl").touch()


def _tool(directory: pathlib.Path, call_id: str, tool_name: str, tool_input: dict, response: object, *, codex: bool) -> None:
    """公開hookのPreToolUseとPostToolUseへ、同じ呼び出しの入力と応答を供給する。"""
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "tool_name": tool_name,
        "tool_use_id": call_id,
        "transcript_path": str(directory / "transcript.jsonl"),
        "cwd": str(directory),
        "tool_input": tool_input,
    }
    if codex:
        payload["turn_id"] = "codex-turn"
    with contextlib.redirect_stdout(io.StringIO()):
        pretooluse.main(json.dumps(payload))
    payload["tool_response"] = response
    with contextlib.redirect_stdout(io.StringIO()):
        posttooluse.main(json.dumps(payload))


def supply_commit(directory: pathlib.Path, call_id: str = "commit", *, codex: bool = False) -> None:
    """成果作業として成功したcommitの実行記録を供給する。"""
    response: object = "[develop 1234567] 変更" if codex else {"stdout": "[develop 1234567] 変更", "exit_code": 0}
    _tool(directory, call_id, "Bash", {"command": "git commit -m 変更"}, response, codex=codex)


def supply_completion(directory: pathlib.Path, call_id: str = "completion", *, codex: bool = False) -> None:
    """completion-reportの起動証拠を、Claude CodeはSkillの成功、CodexはSKILL.mdの読取で供給する。"""
    if codex:
        skill = directory / "plugin" / "skills" / "completion-report" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(COMPLETION_SKILL_TEXT, encoding="utf-8")
        _tool(directory, call_id, "Bash", {"command": f"cat {skill}"}, COMPLETION_SKILL_TEXT, codex=True)
    else:
        _tool(directory, call_id, "Skill", {"skill": "agent-toolkit:completion-report"}, "起動", codex=False)


def human_input(directory: pathlib.Path, turn_id: str, prompt: str) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        user_prompt_submit.main(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "turn_id": turn_id,
                    "prompt": prompt,
                    "transcript_path": str(directory / "transcript.jsonl"),
                }
            )
        )


def stop_payload(directory: pathlib.Path, visible: str, *, codex: bool = False) -> str:
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "transcript_path": str(directory / "transcript.jsonl"),
        "stop_hook_active": False,
        "last_assistant_message": visible,
    }
    if codex:
        payload["turn_id"] = "codex-turn"
    return json.dumps(payload)


@pytest.mark.parametrize("codex", [False, True])
def test_missing_completion_blocks_from_first_stop_and_evidence_allows(tmp_path: pathlib.Path, *, codex: bool) -> None:
    """両ホストとも起動証拠の無い成果作業を初回Stopで遮断し、実際の起動証拠を得た後は許可する。"""
    supply_commit(tmp_path, codex=codex)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE, codex=codex))
    assert decision == "block"
    assert "completion-report" in reason and "commit" in reason
    assert ("SKILL.md" in reason) is codex
    supply_completion(tmp_path, codex=codex)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE, codex=codex)) == ("approve", "")


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "visible",
    [
        WORK_COMPLETE,
        WORK_COMPLETE.replace("## 作業完了報告", "## 完了報告"),
        "## 振り返り結果報告\n\n### 確定した問題と対策\n\n- 実装済み: 対策。根拠: 変更\n",
        "## 振り返り結果報告\n\n### 対策を見送った問題\n\n- 判定済み: 問題\n",
        "## 振り返り結果の予告\n\n### 確定した問題と対策\n\n- AWI登録予定: 対策\n",
        "## AWI投入結果報告\n\n- AWI登録予定: 未投入の対策（c0006）\n",
        "終了する。",
        "",
    ],
)
def test_report_body_differences_do_not_block_evidenced_work(tmp_path: pathlib.Path, visible: str, *, codex: bool) -> None:
    """起動証拠を持つ作業は、見出し・句読点・区分・候補ID・予定を示す語だけが異なる本文で遮断しない。"""
    supply_commit(tmp_path, codex=codex)
    supply_completion(tmp_path, codex=codex)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, visible, codex=codex)) == ("approve", "")


@pytest.mark.parametrize(
    ("command", "response"),
    [
        ("echo plugin/skills/completion-report/SKILL.md", "plugin/skills/completion-report/SKILL.md"),
        ("cat plugin/skills/completion-report/SKILL.md", "cat: plugin/skills/completion-report/SKILL.md: No such file"),
        ("grep -n name plugin/skills/completion-report/SKILL.md", "2:name: completion-report"),
        ("cat plugin/skills/session-review/SKILL.md", "---\nname: session-review\n---\n"),
        ("cat plugin/skills/completion-report/SKILL.md", ""),
    ],
)
def test_codex_path_string_or_failed_read_is_not_evidence(tmp_path: pathlib.Path, command: str, response: str) -> None:
    """読取パスの文字列、失敗・空の読取、検索の一致行、他のスキルの読取を起動証拠にしない。"""
    supply_commit(tmp_path, codex=True)
    _tool(tmp_path, "read", "Bash", {"command": command}, response, codex=True)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE, codex=True))[0] == "block"


def test_codex_saved_output_of_read_is_evidence(tmp_path: pathlib.Path) -> None:
    """`atk read-file`が全量を保存した場合は、保存先の本文で読取の結果を確かめる。"""
    supply_commit(tmp_path, codex=True)
    saved = tmp_path / "saved.txt"
    saved.write_text(json.dumps({"text": COMPLETION_SKILL_TEXT, "eof": True}, ensure_ascii=False), encoding="utf-8")
    command = "atk read-file -- /plugin/skills/completion-report/SKILL.md"
    _tool(tmp_path, "read", "Bash", {"command": command}, f"保存先: {saved}\n行数: 1", codex=True)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "", codex=True)) == ("approve", "")


def test_claude_failed_or_unrelated_skill_is_not_evidence(tmp_path: pathlib.Path) -> None:
    """失敗したcompletion-reportの起動と、他のスキルの起動を起動証拠にしない。"""
    supply_commit(tmp_path)
    payload: dict[str, Any] = {
        "session_id": "evidence-test",
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Skill",
        "tool_use_id": "failed-skill",
        "tool_input": {"skill": "agent-toolkit:completion-report"},
        "tool_response": {"error": "失敗"},
    }
    termination_evidence.observe_tool(json.dumps(payload), after=True)
    _tool(tmp_path, "other", "Skill", {"skill": "agent-toolkit:session-review"}, "起動", codex=False)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))[0] == "block"


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "codex", "label"),
    [
        ("Skill", {"skill": "agent-toolkit:process-wi"}, False, "agent-toolkit:process-wiの開始"),
        ("Skill", {"skill": "add-awi-by-user"}, False, "agent-toolkit:add-awi-by-userの開始"),
        ("Bash", {"command": "atk wi add --type awi --file /tmp/body.md"}, False, "WIの保存"),
        ("Bash", {"command": "atk run-script plan-create -- --title 計画"}, False, "計画の保存"),
        ("Bash", {"command": "git push origin develop"}, True, "push"),
    ],
)
def test_deliverable_kinds_require_completion(
    tmp_path: pathlib.Path, tool_name: str, tool_input: dict, codex: bool, label: str
) -> None:
    """対象スキルの開始、WI・計画の保存、pushを成果作業として示す。"""
    response: object = "起動" if tool_name == "Skill" else ("出力" if codex else {"stdout": "出力", "exit_code": 0})
    _tool(tmp_path, "deliverable", tool_name, tool_input, response, codex=codex)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "", codex=codex))
    assert decision == "block" and label in reason


def test_codex_target_skill_read_is_deliverable(tmp_path: pathlib.Path) -> None:
    """Codexが対象スキルの`SKILL.md`を実際に読んだ記録を、対象スキルの開始として扱う。"""
    text = "---\nname: process-wi\n---\n\n# WI処理\n"
    _tool(tmp_path, "start", "Bash", {"command": "sed -n '1,200p' plugin/skills/process-wi/SKILL.md"}, text, codex=True)
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "", codex=True))
    assert decision == "block" and "agent-toolkit:process-wiの開始" in reason


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "response"),
    [
        ("Bash", {"command": "git commit --dry-run -m 変更"}, {"stdout": "", "exit_code": 0}),
        ("Bash", {"command": "git commit -m 変更"}, {"stdout": "", "exit_code": 1}),
        ("Bash", {"command": "git status"}, {"stdout": "", "exit_code": 0}),
        ("Bash", {"command": "atk wi show 20261001-000000-001.md"}, {"stdout": "", "exit_code": 0}),
        ("Skill", {"skill": "agent-toolkit:writing-standards"}, "起動"),
    ],
)
def test_answer_only_or_failed_work_is_not_deliverable(
    tmp_path: pathlib.Path, tool_name: str, tool_input: dict, response: object
) -> None:
    """回答のための照会、試行・失敗した操作、成果作業の対象外のスキルは起動不足として遮断しない。"""
    _tool(tmp_path, "call", tool_name, tool_input, response, codex=False)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "回答した。")) == ("approve", "")


def test_finished_work_is_not_reused_after_new_input(tmp_path: pathlib.Path) -> None:
    """起動証拠を得た作業の後の新しい依頼による成果作業へ、前の起動証拠を流用しない。"""
    supply_commit(tmp_path, "commit-1")
    supply_completion(tmp_path, "completion-1")
    human_input(tmp_path, "next", "次の作業を頼む")
    supply_commit(tmp_path, "commit-2")
    works = termination_evidence.session_works(json.loads(stop_payload(tmp_path, "")))
    assert len(works) == 2
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))
    assert decision == "block" and f"作業 {works[1][0]}:" in reason and "work-1:" not in reason


def test_completion_before_deliverable_in_same_work_is_evidence(tmp_path: pathlib.Path) -> None:
    """同じ作業の開始後に得た起動証拠は、その後の成果作業（振り返りの実装など）にも有効とする。"""
    human_input(tmp_path, "request", "作業を頼む")
    supply_completion(tmp_path)
    supply_commit(tmp_path)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")


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
    assert decision == "block" and new_id in reason and "work-1:" not in reason


def test_cancelling_current_work_keeps_missing_completion_of_other_work(tmp_path: pathlib.Path) -> None:
    """後から始めた作業の中止は、先の作業に残る起動不足を判定の対象に残す。"""
    supply_commit(tmp_path)
    prompt = "別の作業を始めたが、それは中止する"
    human_input(tmp_path, "switch", prompt)
    document = {"session_id": "evidence-test", "input_id": "switch", "quote": prompt, "reason": "後の作業の中止"}
    later_id = termination_evidence.record_decision({**document, "action": "start"})
    termination_evidence.record_decision({**document, "action": "cancel", "work_id": later_id})
    decision, reason = termination_order_advisor.evaluate(stop_payload(tmp_path, "中止した。"))
    assert decision == "block"
    assert "作業 work-1: commit" in reason
    assert later_id not in reason


def test_waiting_for_answer_is_scoped_and_returns_to_remaining_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supply_commit(tmp_path)
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
    supply_commit(tmp_path)
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


def test_corrupt_or_foreign_state_does_not_block(tmp_path: pathlib.Path) -> None:
    """証拠の状態が別の版・形式で読み取れない場合は、呼び忘れと決め付けずに許可する。"""
    supply_commit(tmp_path)

    def corrupt(state: dict) -> dict:
        state[termination_evidence.STATE_KEY]["version"] = 0
        return state

    session_state.update_state("evidence-test", corrupt)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")


def test_unreadable_transcript_does_not_block(tmp_path: pathlib.Path) -> None:
    """待機対象を確かめるtranscriptを読み取れない場合は遮断しない。"""
    supply_commit(tmp_path)
    (tmp_path / "transcript.jsonl").unlink()
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "")) == ("approve", "")


def test_async_wait_is_scoped_to_real_child_and_returns_after_end(tmp_path: pathlib.Path) -> None:
    supply_commit(tmp_path)
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
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "待機中"))[0] == "approve"
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
    supply_commit(tmp_path)
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
    human_input(tmp_path, "human-1", "作業を頼む")
    supply_commit(tmp_path)
    reason = termination_order_advisor.evaluate(stop_payload(tmp_path, WORK_COMPLETE))[1]
    assert "evidence-test" in reason and "human-1" in reason and "work-1" in reason


def test_public_wait_decision_uses_cli_lock_after_observation_attempt(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """委譲開始とPostToolUseの観測更新を経て、CLIの待機所有権で公開wait判断を受理する。"""
    supply_commit(tmp_path)
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
    assert decision == "block" and "completion-report" in reason


@pytest.mark.parametrize("kind", ["unrelated", "other-owner", "finished"])
def test_explicit_child_wait_rejection_is_not_uwi_guidance(tmp_path: pathlib.Path, kind: str) -> None:
    """作業不一致・所有者違い・待機解消を対象ごとの理由で拒否する。"""
    supply_commit(tmp_path)

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


@pytest.mark.parametrize(
    "invalid",
    [
        "quote",
        "policy-outside",
        "target",
        "target-other",
        "another-repo",
        "work",
        "session",
        "agent",
        "runtime-agent",
        "input",
        "old-input",
        "generated",
    ],
)
def test_omission_cli_rejects_unrelated_evidence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, invalid: str) -> None:
    """省略の引用・対象・会話・担当・入力が実体と異なる場合は、CLIから不足を免除できない。"""
    from agent_toolkit._testing import git_repository  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    repository = git_repository.init_repository(tmp_path / "repo", files={"CLAUDE.md": "完了報告は指示まで省く。\n"})
    trace = tmp_path / "transcript.jsonl"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "evidence-test")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    human_input(tmp_path, "request", "成果を作成する")
    termination_evidence.observe_tool(
        json.dumps(
            {
                "session_id": "evidence-test",
                "cwd": str(repository),
                "tool_name": "Edit",
                "tool_use_id": "edit",
                "tool_input": {"file_path": str(repository / "CLAUDE.md")},
                "tool_response": {"exit_code": 0},
            }
        ),
        after=True,
    )
    document = {
        "session_id": "evidence-test",
        "action": "omit-completion",
        "work_id": "work-1",
        "input_id": "request",
        "reason": "適用規範に従う",
        "policy_file": str(repository / "CLAUDE.md"),
        "quote": "完了報告は指示まで省く。\n",
        "target_repo": str(repository),
    }
    if invalid == "quote":
        document["quote"] = "異なる本文"
    elif invalid == "policy-outside":
        policy = tmp_path / "CLAUDE.md"
        policy.write_text(document["quote"], encoding="utf-8")
        document["policy_file"] = str(policy)
    elif invalid == "target":
        document["target_repo"] = str(tmp_path / "missing")
    elif invalid == "target-other":
        other = git_repository.init_repository(tmp_path / "other", files={"CLAUDE.md": document["quote"]})
        document.update(target_repo=str(other), policy_file=str(other / "CLAUDE.md"))
    elif invalid == "another-repo":
        other = git_repository.init_repository(tmp_path / "other", files={"README.md": "別の成果\n"})
        termination_evidence.observe_tool(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "cwd": str(repository),
                    "tool_name": "Bash",
                    "tool_use_id": "other-commit",
                    "tool_input": {"command": f"git -C {other} commit -m 変更"},
                    "tool_response": {"exit_code": 0},
                }
            ),
            after=True,
        )
    elif invalid == "work":
        document["work_id"] = "work-missing"
    elif invalid == "session":
        document["session_id"] = "another-session"
    elif invalid == "agent":
        document["agent_id"] = "child"
    elif invalid == "runtime-agent":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif invalid == "old-input":
        human_input(tmp_path, "new-request", "別の依頼")
    elif invalid == "generated":
        termination_evidence.observe_user(
            json.dumps(
                {
                    "session_id": "evidence-test",
                    "prompt_id": "generated",
                    "prompt": '<atk-auto source="stop" kind="notice">継続</atk-auto>',
                }
            )
        )
        document["input_id"] = "generated"
    else:
        document["input_id"] = "missing-input"
    source = tmp_path / "omission.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert (
            run_script.dispatch(
                argparse.Namespace(script_name="termination-evidence", script_args=["--decision-file", str(source)])
            )
            == 2
        )
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert termination_order_advisor.evaluate(stop_payload(tmp_path, "成果を届けた。"))[0] == "block"
    assert trace.is_file()
