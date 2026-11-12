"""Stopが質問・完遂・送達の意味判断をエージェント向け文書へ委ねる契約。"""

import json

import pytest

from agent_toolkit._common import session_activity
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


@pytest.mark.parametrize("scenario", ["language", "missing-summary", "complete"])
def test_codex_stop_returns_blocking_results(monkeypatch, capsys, scenario):
    """Codexの公開Stopが必要な遮断を返し、Claude専用の通知欄を送らない。"""
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
        monkeypatch.delenv(name, raising=False)
    sid = f"codex-{scenario}"
    message = "The work is complete." if scenario == "language" else "完了した。"
    if scenario == "missing-summary":
        session_activity.record_submission_report(sid, ["指摘の分類と処置:", "- 確認の往復: 2回"])
    payload = {"session_id": sid, "turn_id": "turn", "last_assistant_message": message}
    assert stop.main(json.dumps(payload)) == 0
    output = capsys.readouterr()
    assert not output.err
    result = json.loads(output.out)
    if scenario == "complete":
        assert not result
    else:
        assert result["decision"] == "block"
        assert result["reason"]
    assert "hookSpecificOutput" not in result
    assert "continue" not in result


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
