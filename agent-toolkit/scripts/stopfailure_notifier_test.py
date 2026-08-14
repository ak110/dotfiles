"""agent-toolkit/scripts/stopfailure_notifier.py のテスト。

StopFailure発火内容のログ追記を、時刻固定の引数注入で検証する。
"""

import datetime
import json
import pathlib

import _fork_runner
import pytest
import stopfailure_notifier
from stopfailure_notifier import append_log

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook.py"
_FIXED_NOW = datetime.datetime(2026, 5, 26, 12, 0, 0, tzinfo=datetime.UTC)


def test_append_log_writes_record(tmp_path: pathlib.Path):
    """入力JSON全体とtranscript末尾要約を含む1行を追記する。"""
    transcript = tmp_path / "transcript.jsonl"
    error_entry = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {
            "id": "err",
            "role": "assistant",
            "content": [{"type": "text", "text": "The model's tool call could not be parsed (retry also failed)."}],
        },
    }
    transcript.write_text(json.dumps(error_entry, ensure_ascii=False) + "\n", encoding="utf-8")
    log_path = tmp_path / "stopfailure.jsonl"
    payload = {
        "session_id": "s1",
        "hook_event_name": "StopFailure",
        "cwd": "/work",
        "error_type": "rate_limit",
        "transcript_path": str(transcript),
    }

    append_log(payload, log_path=log_path, now=_FIXED_NOW)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["timestamp"] == "2026-05-26T12:00:00+00:00"
    assert record["session_id"] == "s1"
    assert record["hook_event_name"] == "StopFailure"
    assert record["cwd"] == "/work"
    assert record["input"] == payload
    assert "could not be parsed" in record["transcript_summary"]


def test_append_log_appends_across_sessions(tmp_path: pathlib.Path):
    """セッション横断で同一ログへ追記し、既存行を保持する。"""
    log_path = tmp_path / "stopfailure.jsonl"
    append_log({"session_id": "s1"}, log_path=log_path, now=_FIXED_NOW)
    append_log({"session_id": "s2"}, log_path=log_path, now=_FIXED_NOW)
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["session_id"] == "s1"
    assert json.loads(lines[1])["session_id"] == "s2"


def test_append_log_rotates_one_generation_at_one_megabyte(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値超過済みログを1世代へ退避して新しいレコードを追記する。"""
    log_path = tmp_path / "stopfailure.jsonl"
    log_path.write_text("x" * 11, encoding="utf-8")
    monkeypatch.setattr(stopfailure_notifier, "_LOG_MAX_BYTES", 10)

    append_log({"session_id": "s1"}, log_path=log_path, now=_FIXED_NOW)

    assert log_path.with_suffix(".jsonl.1").read_text(encoding="utf-8") == "x" * 11
    assert json.loads(log_path.read_text(encoding="utf-8"))["session_id"] == "s1"


def test_invalid_json_exits_safely():
    """不正JSON入力でも例外を送出せず正常終了する。"""
    result = _fork_runner.run_script(_SCRIPT, argv=("stopfailure_notifier",), input="not json")
    assert result.returncode == 0
