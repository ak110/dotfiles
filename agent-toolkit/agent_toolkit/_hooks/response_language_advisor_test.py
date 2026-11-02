"""Codexの公開停止入力から共通判定と再出力までを確かめる。"""

import json

import pytest

from agent_toolkit._atk import agents_exit_session
from agent_toolkit._hooks import response_language_advisor, stop, subagent_stop_advisor


@pytest.mark.parametrize("entrypoint", ["parent", "child"])
def test_codex_completion_rewrites_explanation_and_preserves_material(entrypoint, capsys, monkeypatch):
    """直接送達する親の本文を保護し、子の内部返却は言語で遮断しない。"""
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
    if entrypoint == "child":
        assert not blocked
        return
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


@pytest.mark.parametrize("actor", ["main", "subagent", "delegated", "owner", "codex"])
def test_recovery_preserves_delegated_return_contract(actor, monkeypatch):
    """最終本文の受取先ごとに、画面の終了と委譲の返却を保つ回復操作を選ぶ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    payload = {"last_assistant_message": "The task completed successfully."}
    if actor == "subagent":
        payload["agent_id"] = "child"
    elif actor == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif actor == "owner":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "parent")
    elif actor == "codex":
        payload["turn_id"] = "turn"
    decision, reason = response_language_advisor.evaluate(json.dumps(payload))
    if actor in {"subagent", "delegated", "owner"}:
        assert (decision, reason) == ("", "")
        return
    assert decision == "block"
    assert "`…`だけを出力して停止" not in reason
    assert "そのまま再出力して停止" in reason


@pytest.mark.parametrize("delegated", ["0", "1"])
@pytest.mark.parametrize("child", [False, True])
def test_claude_english_completion_rewrites_once(delegated, child, monkeypatch, capsys):
    """直接送達するメインの本文だけを書き直す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", delegated)
    payload: dict[str, object] = {"last_assistant_message": "The work is complete."}
    if child:
        payload["agent_id"] = "child"
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("response_language_advisor",))
    raw = json.dumps(payload)
    if child:
        assert subagent_stop_advisor.main(raw) == 0
        output = capsys.readouterr().out
        blocked = json.loads(output) if output else {}
    else:
        blocked = stop.evaluate(raw)
    if delegated == "1" or child:
        assert not blocked
        return
    decision, reason = blocked["decision"], blocked["reason"]
    assert decision == "block"
    assert isinstance(reason, str)
    assert "日本語で書いてから停止" in reason
    payload["stop_hook_active"] = True
    assert response_language_advisor.evaluate(json.dumps(payload)) == ("", "")


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("marker_body", ["ready", "consumed", None])
def test_stop_language_follows_user_delivery_path(tmp_path, monkeypatch, marker_body, codex):
    """同一会話の送達ツールの準備印があるClaudeだけ、末尾の状態行を除外する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    marker = tmp_path / "marker.txt"
    if marker_body is not None:
        marker.write_text(marker_body, encoding="utf-8")
    monkeypatch.setattr(agents_exit_session, "function_hook_paths", lambda _session: (marker, tmp_path / "request.txt"))
    payload: dict[str, object] = {"session_id": "delivery-path", "last_assistant_message": "The work is complete."}
    if codex:
        payload["turn_id"] = "turn"
    decision, _reason = response_language_advisor.evaluate(json.dumps(payload))
    assert (decision == "block") is (codex or marker_body != "ready")
    payload["stop_hook_active"] = False
    payload["last_assistant_message"] = "作業が完了しました。"
    assert response_language_advisor.evaluate(json.dumps(payload)) == ("", "")
