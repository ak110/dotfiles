"""続行不能の根拠を当turnの実記録へ対応付け、有限再提出を確認する。"""

import json
from pathlib import Path

import pytest

from agent_toolkit._agents_server import record_paths, result_contract, state
from agent_toolkit._common import task_documents

_START = "2026-11-17T00:00:00+00:00"
_CURRENT = "2026-11-17T00:01:00+00:00"
_DIAGNOSTIC = "Connection refused: local runtime service is not listening at 127.0.0.1:43210"


def _records(path: Path, *, timestamp: str = _CURRENT, retry: bool = False) -> None:
    call = {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "service ping"}}
    result = {"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": _DIAGNOSTIC}
    records = [
        {"type": "assistant", "timestamp": timestamp, "message": {"content": [call]}},
        {"type": "user", "timestamp": timestamp, "message": {"content": [result]}},
    ]
    if retry:
        records.extend(
            [
                {"type": "assistant", "timestamp": _CURRENT, "message": {"content": [{**call, "id": "call-2"}]}},
                {
                    "type": "user",
                    "timestamp": _CURRENT,
                    "message": {
                        "content": [{**result, "tool_use_id": "call-2", "content": "service ready", "is_error": False}]
                    },
                },
            ]
        )
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")


def _result(reason: str) -> dict:
    return {
        "status": "completed",
        "agent_message": f"続行できない区分: runtime_unavailable\n続行できない理由: {reason}",
    }


@pytest.mark.parametrize("kind", ["delegate", "explore"])
@pytest.mark.parametrize("role_task", [False, True])
@pytest.mark.parametrize(
    "reason", ["この応答の実行予算では残工程を実行できない", "今回の実行時間予算が不足している", "ツール呼出し枠が残っていない"]
)
def test_unobserved_budget_is_held_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: state.LaunchKind, role_task: bool, reason: str
) -> None:
    """予算という語に限らず、実記録の無い停止を既存の1回の再提出へ返す。"""
    session = state.SessionState("runtime-test", str(tmp_path), launch_kind=kind, started_at=_START)
    if role_task:
        role = Path(__file__).resolve().parents[2] / "share" / "exec.subagent.md"
        session.prompt = task_documents.task_prompt_header(role)
    monkeypatch.setattr(record_paths, "find_session_record", lambda _session_id: None)
    result = _result(reason)
    assert result_contract.hold_invalid_result(session, result)
    session.result_contract_recovery_consumed = True
    assert not result_contract.hold_invalid_result(session, result)
    assert "resultContract" in result["error"]
    assert result["agent_message"].endswith(reason)


@pytest.mark.parametrize(
    ("timestamp", "retry", "accepted"),
    [(_CURRENT, False, True), (_START.replace("17", "16"), False, False), (_CURRENT, True, False)],
)
def test_tool_evidence_is_current_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timestamp: str, retry: bool, accepted: bool
) -> None:
    """逐語診断の空白差を許容するが、前turnと回復した呼出しは根拠にしない。"""
    path = tmp_path / "record.jsonl"
    _records(path, timestamp=timestamp, retry=retry)
    monkeypatch.setattr(record_paths, "find_session_record", lambda _id: record_paths.SessionRecord("claude", (path,)))
    session = state.SessionState("runtime-test", str(tmp_path), started_at=_START)
    reason = "診断: " + _DIAGNOSTIC.replace(" ", "  ") + "。実行機能の再開が必要"
    assert (result_contract.invalid_result_reason(session, _result(reason)) is None) is accepted


@pytest.mark.parametrize(("timestamp", "accepted"), [(_CURRENT, True), ("2026-11-16T23:00:00+00:00", False)])
def test_api_error_must_belong_to_current_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, timestamp: str, accepted: bool
) -> None:
    """終了時の実API障害を保持し、前turnの残存情報だけでは受理しない。"""
    session = state.SessionState("runtime-test", str(tmp_path), started_at=_START)
    session.api_error = {"type": "unavailable", "first_at": timestamp}
    monkeypatch.setattr(record_paths, "find_session_record", lambda _id: None)
    assert (result_contract.invalid_result_reason(session, _result("API接続の復旧が必要")) is None) is accepted


def test_codex_outer_exec_result_is_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Codexの外側exec結果に含まれる下位診断を、親API障害なしで受理する。"""
    path = tmp_path / "codex.jsonl"
    records = [
        {
            "type": "response_item",
            "timestamp": _CURRENT,
            "payload": {"type": "function_call", "name": "functions.exec", "call_id": "exec-1", "arguments": "await tool()"},
        },
        {
            "type": "response_item",
            "timestamp": _CURRENT,
            "payload": {"type": "function_call_output", "call_id": "exec-1", "output": _DIAGNOSTIC},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    monkeypatch.setattr(record_paths, "find_session_record", lambda _id: record_paths.SessionRecord("codex", (path,)))
    session = state.SessionState("runtime-test", str(tmp_path), started_at=_START)
    assert result_contract.invalid_result_reason(session, _result(_DIAGNOSTIC)) is None


@pytest.mark.parametrize("diagnostic", [False, True])
def test_reader_fit_multiline_reason_preserves_finite_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, diagnostic: bool
) -> None:
    """正式な複数行理由も、実診断と自己推定を区別して1回の再提出へ渡す。"""
    path = tmp_path / "record.jsonl"
    _records(path)
    monkeypatch.setattr(record_paths, "find_session_record", lambda _id: record_paths.SessionRecord("claude", (path,)))
    session = state.SessionState("runtime-test", str(tmp_path), launch_kind="explore", started_at=_START)
    role = Path(__file__).resolve().parents[2] / "share" / "reader-fit-review.subagent.md"
    session.prompt = task_documents.task_prompt_header(role) + "\n成果物: /example.md\n"
    reason = _DIAGNOSTIC if diagnostic else "この応答の実行予算では残工程を実行できない"
    result = {
        "status": "completed",
        "agent_message": (
            "- 成果物: /example.md\n  完了状態: blocked\n  比較資料: 指定HEAD\n  根拠: 調査途中\n"
            f"  続行できない区分: runtime_unavailable\n  続行できない理由:\n     {reason}\n"
        ),
    }
    assert (result_contract.invalid_result_reason(session, result) is None) is diagnostic
    assert result_contract.hold_invalid_result(session, result)
    session.result_contract_recovery_consumed = True
    assert not result_contract.hold_invalid_result(session, result)
    assert (result.get("error") is None) is diagnostic
    assert result["status"] == ("completed" if diagnostic else "failed")
