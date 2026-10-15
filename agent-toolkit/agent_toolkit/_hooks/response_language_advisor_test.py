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

    assert invoke()["decision"] == "block"
    payload["last_assistant_message"] = '```json\n[{"retain":"基準の所有位置。引用は「기준의」。"}]\n```'
    assert "decision" not in invoke()
    payload["last_assistant_message"] = "説明の기준의混入"
    payload["stop_hook_active"] = True
    assert "decision" not in invoke()


@pytest.mark.parametrize(
    "payload", [{}, [], {"last_assistant_message": "Done here."}, {"turn_id": "t", "last_assistant_message": None}]
)
def test_unavailable_or_other_host_input_is_not_blocked(payload):
    """取得不能や別ホストを未観測の欠陥へ読み替えない。"""
    assert response_language_advisor.evaluate(json.dumps(payload)) == ("", "")
