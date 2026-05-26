"""agent-toolkit/scripts/stopfailure_notifier.py のテスト。

StopFailure発火内容のログ追記を、時刻固定の引数注入で検証する。
"""

import datetime
import json
import pathlib
import subprocess
import sys

from stopfailure_notifier import append_log

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "stopfailure_notifier.py"
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


def test_invalid_json_exits_safely():
    """不正JSON入力でも例外を送出せず正常終了する。"""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
