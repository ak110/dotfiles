"""Codexの公開停止入力から共通判定と再出力までを確かめる。"""

import json

import pytest

from agent_toolkit._hooks import response_language_advisor, stop, subagent_stop_advisor


@pytest.mark.parametrize("entrypoint", ["parent", "child"])
def test_codex_completion_rewrites_explanation_and_preserves_material(entrypoint, capsys, monkeypatch):
    """親子の同じ混入を遮断し、日本語への書き直しと正当な素材を許可する。"""
    monkeypatch.setattr(stop, "CODEX_CHECK_MODULE_NAMES", ("response_language_advisor",))
    payload: dict[str, object] = {
        "session_id": "language-probe",
        "turn_id": "turn",
        "last_assistant_message": '```json\n[{"retain":"基準の기준의所有位置"}]\n```',
    }

    def invoke():
        raw = json.dumps(payload, ensure_ascii=False)
        if entrypoint == "parent":
            return stop.evaluate(raw)
        assert subagent_stop_advisor.main(raw) == 0
        output = capsys.readouterr().out
        return json.loads(output) if output else {}

    blocked = invoke()
    assert blocked["decision"] == "block"
    reason = blocked["reason"]
    assert isinstance(reason, str)
    for expected in ("判定基準", "基準の기준의所有位置", "完了本文を変更せず", "そのまま再出力して停止"):
        assert expected in reason
    assert "`…`だけ" not in reason
    original_report = payload["last_assistant_message"]
    payload["stop_hook_active"] = True
    assert "decision" not in invoke()
    assert payload["last_assistant_message"] == original_report
    payload["stop_hook_active"] = False
    payload["last_assistant_message"] = '```json\n[{"retain":"基準の所有位置。引用は「기준의」。"}]\n```'
    assert "decision" not in invoke()
    payload["last_assistant_message"] = "説明の기준의混入"
    payload["stop_hook_active"] = True
    assert "decision" not in invoke()


@pytest.mark.parametrize("payload", [{}, [], {"turn_id": "t", "last_assistant_message": None}])
def test_unavailable_or_other_host_input_is_not_blocked(payload):
    """取得不能や別ホストを未観測の欠陥へ読み替えない。"""
    assert response_language_advisor.evaluate(json.dumps(payload)) == ("", "")


@pytest.mark.parametrize("delegated", ["0", "1"])
@pytest.mark.parametrize("child", [False, True])
def test_claude_english_completion_rewrites_once(delegated, child, monkeypatch, capsys):
    """メイン、委譲先、サブエージェントの英語の最終応答を書き直す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", delegated)
    payload: dict[str, object] = {"last_assistant_message": "The work is complete."}
    if child:
        payload["agent_id"] = "child"
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("response_language_advisor",))
    raw = json.dumps(payload)
    if child:
        assert subagent_stop_advisor.main(raw) == 0
        blocked = json.loads(capsys.readouterr().out)
    else:
        blocked = stop.evaluate(raw)
    decision, reason = blocked["decision"], blocked["reason"]
    assert decision == "block"
    assert isinstance(reason, str)
    assert "日本語で書いてから停止" in reason
    payload["stop_hook_active"] = True
    assert response_language_advisor.evaluate(json.dumps(payload)) == ("", "")
    payload["stop_hook_active"] = False
    payload["last_assistant_message"] = "作業が完了しました。"
    assert response_language_advisor.evaluate(json.dumps(payload)) == ("", "")
