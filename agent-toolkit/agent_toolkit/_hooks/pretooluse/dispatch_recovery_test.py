"""正常拒否と、引用を保った質問・計画の再発行を検証する。"""

# 対象tool集合の追加漏れと内部出力の分離を契約として検証する。
# pylint: disable=protected-access

import json

import pytest

from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit._hooks.pretooluse.decision import Decision


@pytest.fixture(autouse=True)
def _isolate_read_guidance(monkeypatch):
    """言語と正常拒否の公開出力を、資料選択の処理時間から分離する。"""

    def delivered(*_args, **_kwargs):
        return Decision()

    monkeypatch.setattr(dispatch, "read_delivery", delivered)


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


def test_new_language_target_requires_recovery_case() -> None:
    with pytest.raises(AssertionError, match="回復試験を追加"):
        _input("NewUserFacingTool", "素材")


@pytest.mark.parametrize("tool", sorted(dispatch._USER_FACING_TEXT_TOOL_NAMES))
def test_intervening_operation_invalidates_recovery(tool, capsys) -> None:
    # 引用された英語原文を保持するテストコード。
    text = '原文: "Now the remote process no longer exists and the requested action has failed."'
    payload = {"session_id": "intervening", "agent_id": "one"}
    assert _reason(payload, tool, _input(tool, text), capsys)
    assert _reason(payload, "Read", {}, capsys) is None
    assert _reason(payload, tool, _input(tool, text), capsys)
