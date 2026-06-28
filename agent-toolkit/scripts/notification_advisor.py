#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Notification hook（idle_prompt）。

入力待ち通知の発火時に、直前ターンがツール呼び出し解析失敗でAPIエラー停止したかを判定する。
停止していた場合のみ、ベルとデスクトップ通知の端末エスケープシーケンスを`terminalSequence`で送出する。
正常完了後の入力待ちでは何も出力しない。

ツール呼び出し解析失敗は観測専用イベント`StopFailure`を発火させ、会話を継続できる`Stop`は発火しない。
さらに`StopFailure`は出力が無視され、commandフックは制御端末（/dev/tty）を持たない。
そのため停止の通知はこのNotificationフックの`terminalSequence`経由で行う。
"""

import json
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _transcript import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    assistant_text,
    latest_main_assistant_entry,
)

# ツール呼び出し解析失敗のAPIエラー本文に含まれるマーカー。本文の微変更に耐えるため部分一致で判定する。
_PARSE_FAILURE_MARKER = "could not be parsed"

# デスクトップ通知のタイトルと本文。ユーザーが直接読むため敬体・日本語で記述する。
_NOTIFY_TITLE = "Claude Code"
_NOTIFY_BODY = "ツール呼び出しの解析に失敗し、応答が中断しました。再開の操作が必要です。"


def _is_api_error_stop(transcript_path: str) -> bool:
    """直前ターンがツール呼び出し解析失敗でAPIエラー停止したかを判定する。

    末尾の非sidechain assistantエントリが`isApiErrorMessage`かつ解析失敗マーカーを含む場合に真を返す。
    """
    entry = latest_main_assistant_entry(transcript_path)
    if entry is None or entry.get("isApiErrorMessage") is not True:
        return False
    return _PARSE_FAILURE_MARKER in assistant_text(entry.get("message"))


def _build_terminal_sequence(body: str, *, title: str = _NOTIFY_TITLE) -> str:
    """ベルとデスクトップ通知の端末エスケープシーケンス（BEL・OSC 9・OSC 777）を連結する。

    OSC 9はiTerm2・Windows Terminal・WezTerm系、OSC 777はurxvt・Ghostty・Warp系の通知に対応する。
    端末側が対応するシーケンスのみ反映され、非対応のシーケンスは無視される。
    """
    bel = "\a"
    osc9 = f"\x1b]9;{body}\x07"
    osc777 = f"\x1b]777;notify;{title};{body}\x07"
    return bel + osc9 + osc777


def main() -> int:
    """Notification hook（idle_prompt）のエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if _is_api_error_stop(transcript_path):
        print(json.dumps({"terminalSequence": _build_terminal_sequence(_NOTIFY_BODY)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- hook自身の異常終了をホスト側プロセスへ波及させないため広範に捕捉（fail-open）
        traceback.print_exc()
        sys.exit(0)
