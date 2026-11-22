"""正常拒否と、引用を保った質問・計画の再発行を検証する。"""

# 対象tool集合の追加漏れと内部出力の分離を契約として検証する。
# pylint: disable=protected-access

import json

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._hooks.pretooluse import dispatch, operation_skills
from agent_toolkit._hooks.pretooluse.decision import Decision


@pytest.fixture(autouse=True)
def _isolate_skill_notices(monkeypatch):
    """言語と正常拒否の公開出力を、操作の直前のスキルの案内から分離する。"""
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])


def _reason(payload: dict, tool: str, inputs: dict, capsys) -> str | None:
    assert dispatch.main(json.dumps({**payload, "tool_name": tool, "tool_input": inputs})) == 0
    captured = capsys.readouterr()
    assert not captured.err
    specific = json.loads(captured.out)["hookSpecificOutput"] if captured.out else {}
    if specific.get("permissionDecision") == "deny":
        assert specific["permissionDecisionReason"]
        return specific["permissionDecisionReason"]
    return None


def _input(tool: str, text: str) -> dict:
    if tool == "ExitPlanMode":
        return {"plan": text}
    if tool == "AskUserQuestion":
        return {"questions": [{"question": text, "header": "確認", "options": []}]}
    raise AssertionError(f"言語判定対象の入力と回復試験を追加せよ: {tool}")


def test_structured_denial_separates_pending_notice(capsys) -> None:
    assert dispatch._emit(Decision(block="拒否理由", notices=("付随通知",)), ["保留通知"], is_codex=False) == 0
    captured = capsys.readouterr()
    assert not captured.err
    specific = json.loads(captured.out)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"] == "拒否理由"
    assert "保留通知" in specific["additionalContext"]
    assert "付随通知" in specific["additionalContext"]


@pytest.mark.parametrize(
    ("tool", "field"),
    [
        ("mcp__plugin_agent-toolkit_agents_server__start", "prompt"),
        ("mcp__plugin_agent-toolkit_agents_server__send_message", "prompt"),
        ("mcp__plugin_agent-toolkit_agents_server__send_to_user", "message"),
        ("mcp__agents_server__send_to_user", "message"),
        ("AskUserQuestion", None),
    ],
)
def test_scope_review_precedes_delivery_and_is_independent_per_decision(tool, field, tmp_path, capsys):
    """範囲を限った過去の決定を引いた場合、対象と原文だけを先に返す。"""
    session_id = "scope-review"
    work_dir = managed_temp.resolve_session_temp(session_id)
    transcript = tmp_path / "scope-review.jsonl"
    offers = [
        "複雑な対処はしない、制限として扱う。この決定は配備と再起動だけの話だよ。",
        "文字起こしツールは中身だけの実装にしておいてほしい。",
    ]
    codex = tool.startswith("mcp__agents_server__")
    entries = (
        [{"type": "event_msg", "payload": {"type": "user_message", "message": text}} for text in offers]
        if codex
        else [{"type": "user", "message": {"content": text}} for text in offers]
    )
    transcript.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )
    payload = {"session_id": session_id, "transcript_path": str(transcript)}
    if codex:
        payload["turn_id"] = "current"

    def reason(message):
        inputs = {field: message} if field else _input(tool, message)
        return _reason(payload, tool, inputs, capsys)

    first = "前の『複雑な対処はしない』という決定をコード実行の転送方式にも適用する。"
    second = "公開予定の変更に文字起こしツールを含める。"
    assert reason(first) is None
    (work_dir / "work-contract.md").write_text(
        "- 「複雑な対処はしない」。配備と再起動に限る。コード実行の転送方式には適用しない。 `scope-review.jsonl:1`\n"
        "- 適用対象: 文字起こしツール。除外: 公開する操作。公開は対象外。 `scope-review.jsonl:2`\n",
        encoding="utf-8",
    )
    assert reason("検証の終了を確認した。") is None
    assert reason("文字起こしツールのテストが通りました。") is None
    assert reason("整理のため読んだ箇所は scope-review.jsonl:1 です。") is None
    first_notice = reason(first) or ""
    assert "まだ送信していない" in first_notice
    assert "複雑な対処はしない、制限として扱う。" in first_notice and offers[1] not in first_notice
    assert "コード実行の転送方式には適用しない" in first_notice
    assert reason(first) is None
    second_notice = reason(second) or ""
    assert offers[1] in second_notice and offers[0] not in second_notice
    assert "公開は対象外" in second_notice
    assert reason(second) is None


@pytest.mark.parametrize("tool", sorted(dispatch._USER_FACING_TEXT_TOOL_NAMES))
def test_quoted_material_can_recover_with_identical_input(tool: str, capsys) -> None:
    # 判定用素材。原文の英語は引用として保持する。
    text = '原文: "Now the requested action has failed because the remote process no longer exists."'
    payload = {"session_id": "recovery", "agent_id": "one"}
    inputs = _input(tool, text)
    first = _reason(payload, tool, inputs, capsys)
    assert first
    assert "引用" in first and "同一" in first
    assert _reason(payload, tool, inputs, capsys) is None


@pytest.mark.parametrize("tool", sorted(dispatch._USER_FACING_TEXT_TOOL_NAMES))
def test_own_english_explanation_remains_blocked_on_identical_retry(tool: str, capsys) -> None:
    # 検出用のエージェント自身の英語説明。
    inputs = _input(tool, "Now I will explain the implementation and ask you to approve the following plan.")
    payload = {"session_id": "recovery", "agent_id": "one"}
    for _ in range(2):
        assert _reason(payload, tool, inputs, capsys)


@pytest.mark.parametrize("tool", sorted(dispatch._USER_FACING_TEXT_TOOL_NAMES))
def test_english_explanation_outside_material_remains_blocked(tool, capsys):
    # 原文の引用とエージェント自身の英語説明を混在させたテストコード。
    text = '原文: "The material is preserved." Now I will explain the implementation in English.'
    payload = {"session_id": "mixed-material", "agent_id": "one"}
    for _ in range(2):
        assert _reason(payload, tool, _input(tool, text), capsys)


@pytest.mark.parametrize("tool", sorted(dispatch._USER_FACING_TEXT_TOOL_NAMES))
def test_non_text_input_change_invalidates_recovery(tool, capsys):
    # 本文が同じ引用素材でも、全入力の一致を要する。
    text = '原文: "Now the requested action has failed because the remote process no longer exists."'
    payload = {"session_id": "all-inputs", "agent_id": "one"}
    inputs = _input(tool, text)
    assert _reason(payload, tool, inputs, capsys)
    assert _reason(payload, tool, {**inputs, "changed_argument": True}, capsys)


def test_language_recovery_is_scoped_to_actor_tool_and_body(capsys) -> None:
    # 英語の入力素材と、それを示す短い日本語本文。
    text = '原文: "Now the requested action has failed because the remote process no longer exists."'
    first = {"session_id": "boundary", "agent_id": "one"}
    second = {"session_id": "boundary", "agent_id": "two"}
    assert _reason(first, "ExitPlanMode", _input("ExitPlanMode", text), capsys)
    assert _reason(second, "ExitPlanMode", _input("ExitPlanMode", text), capsys)
    assert _reason(first, "AskUserQuestion", _input("AskUserQuestion", text), capsys)
    assert _reason(first, "ExitPlanMode", _input("ExitPlanMode", text + "原文。 "), capsys)


@pytest.mark.parametrize("tool", sorted(dispatch._USER_FACING_TEXT_TOOL_NAMES))
def test_intervening_operation_invalidates_recovery(tool, capsys) -> None:
    # 引用された英語原文を保持するテストコード。
    text = '原文: "Now the remote process no longer exists and the requested action has failed."'
    payload = {"session_id": "intervening", "agent_id": "one"}
    assert _reason(payload, tool, _input(tool, text), capsys)
    assert _reason(payload, "Read", {}, capsys) is None
    assert _reason(payload, tool, _input(tool, text), capsys)


def test_generic_reissues_survive_other_operations_and_are_consumed(capsys) -> None:
    """複数依頼は別操作の後でも各1回だけ再発行できる。"""
    payload = {"session_id": "generic-many", "agent_id": "one"}
    first = {"subagent_type": "Explore", "prompt": "調査する。"}
    second = {"subagent_type": "Plan", "prompt": "計画する。"}
    assert _reason(payload, "Agent", first, capsys)
    assert _reason(payload, "Agent", second, capsys)
    assert _reason(payload, "Read", {}, capsys) is None
    assert _reason(payload, "Agent", first, capsys) is None
    assert _reason(payload, "Agent", second, capsys) is None
    assert _reason(payload, "Agent", first, capsys)
    assert _reason(payload, "Agent", second, capsys)


@pytest.mark.parametrize("difference", ["actor", "tool", "input", "session"])
def test_generic_retry_requires_same_session_actor_tool_and_full_input(difference, capsys) -> None:
    """別主体・別ツール・本文以外の引数・別会話の記録は流用しない。"""
    payload = {"session_id": "generic-scope", "agent_id": "one"}
    inputs = {"subagent_type": "Explore", "prompt": "調査する。", "run_in_background": True}
    assert _reason(payload, "Agent", inputs, capsys)
    changed_payload = {**payload}
    changed_input = {**inputs}
    tool = "Agent"
    if difference == "actor":
        changed_payload["agent_id"] = "two"
    elif difference == "session":
        changed_payload["session_id"] = "generic-other"
    elif difference == "tool":
        tool = "Task"
    else:
        changed_input["run_in_background"] = False
    assert _reason(changed_payload, tool, changed_input, capsys)
    assert _reason(payload, "Agent", inputs, capsys) is None


def test_generic_retry_retains_only_latest_32_inputs(capsys) -> None:
    """有限の保持集合から古い入力を除き、直近入力の再発行は通す。"""
    payload = {"session_id": "generic-bounded"}
    for index in range(33):
        assert _reason(payload, "Agent", {"prompt": f"調査{index}"}, capsys)
    assert _reason(payload, "Agent", {"prompt": "調査32"}, capsys) is None
    assert _reason(payload, "Agent", {"prompt": "調査0"}, capsys)
