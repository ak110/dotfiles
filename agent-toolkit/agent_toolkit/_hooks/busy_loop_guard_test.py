"""agent-toolkit/agent_toolkit/_hooks/busy_loop_guard.py のテスト。

常駐ループのセッションで無進捗ターンが続いた場合の停止工程と、
対象外セッション・進捗のあるターン・待機中のターンで何もしない契約を検証する。
"""

import json
import pathlib
import tempfile

import pytest

from agent_toolkit._atk import agents_exit_session as _agents_exit_session
from agent_toolkit._atk.wi import process_loop_log as _process_loop_log
from agent_toolkit._hooks import busy_loop_guard, stop, stop_gate
from agent_toolkit._hooks.output_contract import validate_hook_output
from agent_toolkit._hooks.session_state import read_state

_ENV_REQUIRED = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_ENV_SESSION_ID = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"
_SESSION_ID = "session-busy-loop"


class _Target:
    """停止対象の識別結果を模す。"""

    host = "claude"
    pid = 4242


@pytest.fixture(name="calls")
def _calls(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> dict[str, int]:
    """状態ファイルの位置を隔離し、停止工程の呼び出し回数を記録する。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv(_ENV_REQUIRED, "1")
    monkeypatch.delenv(_ENV_SESSION_ID, raising=False)
    monkeypatch.delenv(_ENV_DELEGATED_SESSION, raising=False)
    monkeypatch.setattr(stop_gate, "is_pending_async_work", lambda *args, **kwargs: False)
    recorded = {"abort": 0, "terminate": 0}

    def _abort() -> pathlib.Path:
        recorded["abort"] += 1
        return tmp_path / "process-wi-abort"

    def _terminate() -> tuple[str, object]:
        recorded["terminate"] += 1
        return "terminating", _Target()

    monkeypatch.setattr(_process_loop_log, "request_abort", _abort)
    monkeypatch.setattr(_agents_exit_session, "request_termination", _terminate)
    return recorded


def _write_transcript(tmp_path: pathlib.Path, name: str, entries: list[dict]) -> str:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    stop_gate._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
    return str(path)


def _text_entry(text: str = "…") -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _tool_use_entry() -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "Bash", "input": {}}]},
    }


def _payload(transcript_path: str, *, agent_id: str | None = None) -> str:
    payload = {"session_id": _SESSION_ID, "transcript_path": transcript_path}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return json.dumps(payload, ensure_ascii=False)


def _turn(tmp_path: pathlib.Path, index: int, entries: list[dict]) -> tuple[str, str]:
    """1ターン分の会話記録を書き、判定結果を返す。"""
    transcript_path = _write_transcript(tmp_path, f"transcript-{index}.jsonl", entries)
    return busy_loop_guard.evaluate(_payload(transcript_path))


def test_halts_after_threshold_no_tool_turns(tmp_path: pathlib.Path, calls: dict[str, int]) -> None:
    """無進捗ターンが閾値回続くと中断要求と終了要求を実行する。"""
    entries: list[dict] = []
    decisions = []
    for index in range(3):
        entries = [*entries, _text_entry()]
        decisions.append(_turn(tmp_path, index, entries))

    assert [decision for decision, _ in decisions[:2]] == ["approve", "approve"]
    assert calls == {"abort": 1, "terminate": 1}
    decision, body = decisions[2]
    assert decision == "notify_user"
    assert "無進捗のターンが3回続いた" in body
    assert "pid=4242" in body
    assert read_state(_SESSION_ID)["stop_no_tool_turn_count"] == 0


def test_below_threshold_keeps_session(tmp_path: pathlib.Path, calls: dict[str, int]) -> None:
    """閾値未満では停止工程を実行せず、連続回数だけを増やす。"""
    entries: list[dict] = []
    for index in range(2):
        entries = [*entries, _text_entry()]
        decision, _ = _turn(tmp_path, index, entries)
        assert decision == "approve"

    assert calls == {"abort": 0, "terminate": 0}
    assert read_state(_SESSION_ID)["stop_no_tool_turn_count"] == 2


def test_tool_use_resets_count(tmp_path: pathlib.Path, calls: dict[str, int]) -> None:
    """ツール呼び出しを含むターンで連続回数が0へ戻る。"""
    entries: list[dict] = [_text_entry(), _text_entry()]
    _turn(tmp_path, 0, entries)
    entries = [*entries, _tool_use_entry()]
    decision, _ = _turn(tmp_path, 1, entries)

    assert decision == "approve"
    assert read_state(_SESSION_ID)["stop_no_tool_turn_count"] == 0
    assert calls == {"abort": 0, "terminate": 0}


def test_pending_async_work_resets_count(
    tmp_path: pathlib.Path,
    calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲先又は背景ジョブの完了待ちのターンは無進捗として数えない。"""
    monkeypatch.setattr(busy_loop_guard, "is_pending_async_work", lambda *args, **kwargs: True)
    entries: list[dict] = []
    for index in range(4):
        entries = [*entries, _text_entry()]
        decision, _ = _turn(tmp_path, index, entries)
        assert decision == "approve"

    assert calls == {"abort": 0, "terminate": 0}
    assert read_state(_SESSION_ID)["stop_no_tool_turn_count"] == 0


def test_non_process_loop_session_is_skipped(
    tmp_path: pathlib.Path,
    calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """常駐ループ外のセッションでは判定を行わない。"""
    monkeypatch.delenv(_ENV_REQUIRED, raising=False)
    entries: list[dict] = []
    for index in range(4):
        entries = [*entries, _text_entry()]
        decision, _ = _turn(tmp_path, index, entries)
        assert decision == "approve"

    assert calls == {"abort": 0, "terminate": 0}
    assert "stop_no_tool_turn_count" not in read_state(_SESSION_ID)


def test_nested_session_id_is_skipped(tmp_path: pathlib.Path, calls: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """別会話IDへ伝播した環境印では親の空転処理を動かさない。"""
    monkeypatch.setenv(_ENV_SESSION_ID, "parent")
    for index in range(4):
        _turn(tmp_path, index, [_text_entry()])
    assert calls == {"abort": 0, "terminate": 0}
    assert "stop_no_tool_turn_count" not in read_state(_SESSION_ID)


def test_matching_session_id_is_counted(tmp_path: pathlib.Path, calls: dict[str, int], monkeypatch: pytest.MonkeyPatch) -> None:
    """会話IDが一致した場合は親の空転判定を続ける。"""
    monkeypatch.setenv(_ENV_SESSION_ID, _SESSION_ID)
    _turn(tmp_path, 0, [_text_entry()])
    assert read_state(_SESSION_ID)["stop_no_tool_turn_count"] == 1
    assert calls == {"abort": 0, "terminate": 0}


def test_delegated_session_is_skipped(
    tmp_path: pathlib.Path,
    calls: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲先セッションでは判定を行わない。"""
    monkeypatch.setenv(_ENV_DELEGATED_SESSION, "1")
    entries: list[dict] = []
    for index in range(4):
        entries = [*entries, _text_entry()]
        decision, _ = _turn(tmp_path, index, entries)
        assert decision == "approve"

    assert calls == {"abort": 0, "terminate": 0}


def test_native_subagent_stop_does_not_abort_parent_loop(tmp_path: pathlib.Path, calls: dict[str, int]) -> None:
    """親の常駐環境を継承するネイティブ委譲先は親の中断要求を作成しない。"""
    entries: list[dict] = []
    for index in range(3):
        entries = [*entries, _text_entry()]
        transcript_path = _write_transcript(tmp_path, f"native-stop-{index}.jsonl", entries)
        result = stop.evaluate(_payload(transcript_path, agent_id="agent-review"))
        assert validate_hook_output("Stop", result) == []
        assert result.get("decision") != "block"

    assert calls == {"abort": 0, "terminate": 0}
    assert "stop_no_tool_turn_count" not in read_state(_SESSION_ID)


def test_native_subagent_question_does_not_block_parent_loop(tmp_path: pathlib.Path, calls: dict[str, int]) -> None:
    """最上位向けの確認フックは委譲先の差し戻し質問を遮断しない。"""
    transcript_path = _write_transcript(tmp_path, "native-question.jsonl", [_text_entry("どちらを選びますか？")])

    result = stop.evaluate(_payload(transcript_path, agent_id="agent-review"))

    assert validate_hook_output("Stop", result) == []
    assert result.get("decision") != "block"
    assert calls == {"abort": 0, "terminate": 0}


def test_stop_entry_point_reports_system_message(tmp_path: pathlib.Path, calls: dict[str, int]) -> None:
    """共通入口が停止の本文を`systemMessage`へ集約する。

    同じターンで別の判定が遮断を返す場合も、利用者向けの本文を同じ応答へ添える。
    """
    entries: list[dict] = []
    result: dict = {}
    for index in range(3):
        entries = [*entries, _text_entry()]
        transcript_path = _write_transcript(tmp_path, f"stop-{index}.jsonl", entries)
        result = stop.evaluate(_payload(transcript_path))

    assert calls["terminate"] == 1
    assert "無進捗のターンが3回続いた" in str(result["systemMessage"])
    assert validate_hook_output("Stop", result) == []
