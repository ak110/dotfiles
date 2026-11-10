"""Stopが質問・完遂・送達の意味判断をエージェント向け文書へ委ねる契約。"""

import json

import pytest

from agent_toolkit._hooks import stop
from agent_toolkit._testing.helpers import _write_transcript


@pytest.mark.parametrize("text", ["### 機能が足りないのか？", "不明点があれば教えてください。", "次はどちらにするか？"])
def test_stop_leaves_question_meaning_to_agent(tmp_path, monkeypatch, text):
    """疑問符や定型表現を含む本文だけでは終了を差し戻さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    trace = _write_transcript(tmp_path, [{"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}])
    result = stop.evaluate(json.dumps({"session_id": "semantic-boundary", "transcript_path": str(trace)}))
    assert result.get("decision") != "block"
    assert "pending_question_advisor" not in stop.CHECK_MODULE_NAMES


def test_codex_stop_keeps_only_blocking_checks():
    """非遮断通知を配送できないCodexのStopは、遮断で扱う言語保護と投入作業の報告確認だけを実行する。"""
    assert stop.CODEX_CHECK_MODULE_NAMES == ("response_language_advisor", "submission_report_advisor")


@pytest.mark.parametrize("runtime", ("claude", "codex", "agy"))
@pytest.mark.parametrize("task_type", (None, "Agent", "Bash", "MCP"))
def test_stop_language_distinguishes_pending_async_work(tmp_path, monkeypatch, runtime, task_type):
    """途中待機の免除はClaudeだけに限り、他ホストの完了本文を保護する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("response_language_advisor",))
    monkeypatch.setattr(stop, "CODEX_CHECK_MODULE_NAMES", ("response_language_advisor",))
    payload: dict[str, object] = {
        "session_id": tmp_path.name,
        "background_tasks": [] if task_type is None else [{"type": task_type, "id": "pending-task"}],
        "last_assistant_message": "The work is complete.",
    }
    if runtime == "codex":
        payload["turn_id"] = "turn"
    elif runtime == "agy":
        payload["runtime"] = "agy"
    result = stop.evaluate(json.dumps(payload))
    assert (result.get("decision") == "block") is (runtime != "claude" or task_type is None)
    payload["stop_hook_active"] = True
    assert stop.evaluate(json.dumps(payload)).get("decision") != "block"
    payload["stop_hook_active"] = False
    payload["agent_id"] = "child"
    assert stop.evaluate(json.dumps(payload)).get("decision") != "block"


def test_reported_background_wait_is_not_language_blocked(tmp_path, monkeypatch):
    """背景Bashを待つ実際の報告文を、待機を終える指示へ読み替えない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("response_language_advisor",))
    payload: dict[str, object] = {
        "session_id": tmp_path.name,
        "background_tasks": [{"type": "Bash", "id": "bkfu06g1l"}],
        "last_assistant_message": "The background wait (bkfu06g1l) is running; I'll pick up results when it completes.",
    }
    assert stop.evaluate(json.dumps(payload)).get("decision") != "block"
