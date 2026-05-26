"""agent-toolkit/scripts/notification_advisor.py のテスト。

idle_prompt通知でのAPIエラー停止判定とterminalSequence出力を、CLI（stdin/stdout）経由で検証する。
"""

import json
import pathlib
import subprocess
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "notification_advisor.py"


def _write_transcript(tmp_path: pathlib.Path, lines: list[dict]) -> pathlib.Path:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return transcript


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )


def _api_error_entry(text: str) -> dict:
    return {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {"id": "err", "role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _assistant_entry(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"id": "m1", "role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"},
    }


def _user_entry(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_api_error_stop_emits_terminal_sequence(tmp_path: pathlib.Path):
    """解析失敗のAPIエラー停止後はベルとOSC通知のシーケンスを出力する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _user_entry("質問"),
            _api_error_entry("The model's tool call could not be parsed (retry also failed)."),
            {"type": "system", "subtype": "turn_duration"},
        ],
    )
    result = _run({"hook_event_name": "Notification", "transcript_path": str(transcript)})
    assert result.returncode == 0
    sequence = json.loads(result.stdout)["terminalSequence"]
    assert "\a" in sequence  # BEL
    assert "\x1b]9;" in sequence  # OSC 9
    assert "\x1b]777;notify;" in sequence  # OSC 777


def test_normal_completion_no_notification(tmp_path: pathlib.Path):
    """正常完了後の入力待ちでは通知を出力しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _user_entry("質問"),
            _assistant_entry("完了しました。"),
        ],
    )
    result = _run({"hook_event_name": "Notification", "transcript_path": str(transcript)})
    assert result.returncode == 0
    assert not result.stdout.strip()


def test_invalid_json_exits_safely():
    """不正JSON入力でも通知を出力せず正常終了する。"""
    result = _run("not json")
    assert result.returncode == 0
    assert not result.stdout.strip()


def test_sidechain_api_error_ignored(tmp_path: pathlib.Path):
    """sidechain（subagent）のAPIエラーは判定対象外とし、直近の非sidechain assistantで判定する。"""
    sidechain_error = {
        "type": "assistant",
        "isSidechain": True,
        "isApiErrorMessage": True,
        "message": {"id": "side", "role": "assistant", "content": [{"type": "text", "text": "could not be parsed"}]},
    }
    transcript = _write_transcript(
        tmp_path,
        [
            _user_entry("質問"),
            _assistant_entry("完了しました。"),
            sidechain_error,
        ],
    )
    result = _run({"hook_event_name": "Notification", "transcript_path": str(transcript)})
    assert result.returncode == 0
    assert not result.stdout.strip()
