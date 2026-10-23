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


def test_codex_stop_keeps_only_language_protection():
    """非遮断通知を配送できないCodexのStopは言語保護だけを実行する。"""
    assert stop.CODEX_CHECK_MODULE_NAMES == ("response_language_advisor",)
