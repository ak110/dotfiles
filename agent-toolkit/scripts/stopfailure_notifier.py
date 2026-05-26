#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: StopFailure hook。

ターンがAPIエラーで終了したときに発火する観測専用イベント`StopFailure`のフック。
出力と終了コードは無視されるため、通知やブロックはできない。発火内容をログへ追記し、
実機の発火イベントと`error type`の種別を確定する材料とする。

ログはセッション横断の追記式JSONLで、一時ディレクトリ規則に従う固定パスへ出力する。
"""

import datetime
import json
import pathlib
import sys
import tempfile
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _transcript import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    assistant_text,
    latest_main_assistant_entry,
)

# transcript末尾テキスト要約の最大文字数。
_SUMMARY_MAX = 500


def _log_path() -> pathlib.Path:
    """セッション横断のStopFailureログのパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / "claude-agent-toolkit-stopfailure.jsonl"


def _transcript_summary(transcript_path: str) -> str:
    """transcript末尾の非sidechain assistantテキストを先頭から一定長で切り詰めて返す。"""
    entry = latest_main_assistant_entry(transcript_path)
    if entry is None:
        return ""
    return assistant_text(entry.get("message")).strip()[:_SUMMARY_MAX]


def append_log(payload: dict, *, log_path: pathlib.Path, now: datetime.datetime) -> None:
    """StopFailure発火内容をJSONL1行としてログへ追記する。

    入力JSON全体を`input`へ保持し、`error type`の入力フィールド名を後から確定できるようにする。
    """
    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    record = {
        "timestamp": now.isoformat(),
        "session_id": payload.get("session_id", ""),
        "hook_event_name": payload.get("hook_event_name", ""),
        "cwd": payload.get("cwd", ""),
        "input": payload,
        "transcript_summary": _transcript_summary(transcript_path),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    """StopFailure hookのエントリポイント。発火内容をログへ追記する。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    append_log(payload, log_path=_log_path(), now=datetime.datetime.now(datetime.UTC))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(0)
