"""Claude Code plugin agent-toolkit: StopFailure hook。

ターンがAPIエラーで終了したときに発火する観測専用イベント`StopFailure`のフック。
出力と終了コードは無視されるため、通知やブロックはできない。発火内容をログへ追記し、
実機の発火イベントと`error type`の種別を確定する材料とする。
メインのrate_limitは、Function hooksのturn.completeへ1回限りの再開予約要求を渡す。

ログはセッション横断の追記式JSONLで、一時ディレクトリ規則に従う固定パスへ出力する。
"""

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
import json
import pathlib
lazy import datetime
lazy import os
lazy import re
lazy import tempfile

lazy from agent_toolkit._common import delegated_session, host_homes
lazy from agent_toolkit._common.file_lock import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    locked_rotate_and_append as _locked_rotate_and_append,
)
lazy from agent_toolkit._common.transcript import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    assistant_text,
    latest_main_assistant_entry,
)

# transcript末尾テキスト要約の最大文字数。
_SUMMARY_MAX = 500
_LOG_MAX_BYTES = 1_000_000


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
    _locked_rotate_and_append(log_path, json.dumps(record, ensure_ascii=False) + "\n", _LOG_MAX_BYTES)


def main(payload_text: str) -> int:
    """StopFailure hookのエントリポイント。発火内容をログへ追記する。"""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    session_id = payload.get("session_id")
    if (
        payload.get("error") == "rate_limit"
        and not payload.get("agent_id")
        and not delegated_session.is_delegated(os.environ)
        and isinstance(session_id, str)
        and re.fullmatch(r"[A-Za-z0-9_-]+", session_id)
    ):
        request = host_homes.claude_config_dir() / "agent-toolkit-function-hooks" / f"rate-limit-{session_id}.txt"
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text("requested", encoding="utf-8")
    append_log(payload, log_path=_log_path(), now=datetime.datetime.now(datetime.UTC))
    return 0
