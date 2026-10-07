"""Stop系の判定とPreToolUse・PostToolUseが共有する、transcriptのエントリの取得と走査。

記録のファイルは`agent_toolkit._common.transcript`が読み、本モジュールは同じhookプロセスでの解析結果の再利用、
Stop時の書き込み完了の待機、走査範囲（sidechainの扱い）とtool_use・tool_resultの取り出しを担う。
"""

import collections.abc
import json
import time

from agent_toolkit._common import transcript as _transcript

# 同一hookプロセスで複数判定モジュールが同じtranscriptの解析済みエントリを再利用するためのキャッシュ。
# 1エントリー限定（`background_tasks._PENDING_ASYNC_WORK_CACHE`と同じ方式）。`transcript_path`のみをキーとし、
# フラッシュ待機（`wait_for_end_turn`）と解析（`_transcript.read_transcript_entries`）の両方を1回に集約する。
_TRANSCRIPT_ENTRIES_CACHE: dict[str, list[dict]] = {}


def read_transcript_entries_cached(transcript_path: str) -> list[dict]:
    """transcriptのフラッシュ待機と解析を同一プロセス内で1回に集約して返す。

    `is_pending_async_work`と他の判定モジュール（`termination_order_advisor`等）が
    同じStop入力のtranscriptを走査する際、`wait_for_end_turn`によるポーリングと
    `_transcript.read_transcript_entries`によるJSONL解析の重複実行を避ける。
    同一`transcript_path`への2回目以降の呼び出しは、直前の解析結果をそのまま返す。
    `transcript_path`が変わった場合はキャッシュを入れ替える。
    """
    if transcript_path in _TRANSCRIPT_ENTRIES_CACHE:
        return _TRANSCRIPT_ENTRIES_CACHE[transcript_path]
    wait_for_end_turn(transcript_path)
    entries = _transcript.read_transcript_entries(transcript_path)
    _TRANSCRIPT_ENTRIES_CACHE.clear()
    _TRANSCRIPT_ENTRIES_CACHE[transcript_path] = entries
    return entries


def wait_for_end_turn(transcript_path: str, *, timeout: float = 0.3) -> None:
    """Stop hook起動とClaude Code側transcriptフラッシュとのレース状態に対処する。

    Claude Codeはassistant最終メッセージのtranscript書き込みとStop hook起動が
    並行することがあり、hookが読んだ時点で最終assistantエントリが未到着の場合がある。
    末尾走査で最新assistantエントリ（非sidechain）の`stop_reason`が`end_turn`であれば
    フラッシュ完了とみなして即時戻る。未到着なら短時間ポーリングし、`timeout`経過で終了する。
    """
    deadline = time.monotonic() + timeout
    poll = 0.05
    while True:
        lines = _transcript.read_transcript_lines(transcript_path)
        if lines is None:
            return
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if entry.get("type") != "assistant" or entry.get("isSidechain"):
                continue
            message = entry.get("message")
            if isinstance(message, dict) and message.get("stop_reason") == "end_turn":
                return
            # 最新assistantエントリがend_turnではない（tool_use等）→レース状態の可能性あり、
            # ポーリングを継続して最終エントリの到着を待つ。
            break
        if time.monotonic() >= deadline:
            return
        time.sleep(poll)


def entry_in_scan_scope(entry: dict, *, include_sidechain: bool) -> bool:
    """エントリが呼び出し元に応じた走査範囲に含まれる場合に真を返す。"""
    return include_sidechain or entry.get("isSidechain") is not True


def iter_assistant_blocks(entries: list[dict], *, include_sidechain: bool = False) -> collections.abc.Iterator[dict]:
    """走査範囲内のassistantエントリのcontent辞書を時系列で返す。"""
    for entry in entries:
        if entry.get("type") != "assistant" or not entry_in_scan_scope(entry, include_sidechain=include_sidechain):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        yield from (block for block in content if isinstance(block, dict))


def has_tool_use_block(entries: list[dict]) -> bool:
    """走査範囲内のassistantエントリにツール呼び出しが1件以上ある場合に真を返す。

    委譲先（sidechain）のエントリは走査範囲の外とし、自セッションの進捗だけを数える。
    """
    return any(block.get("type") == "tool_use" for block in iter_assistant_blocks(entries))


def last_tool_use_block(entries: list[dict]) -> dict | None:
    """最新assistantメッセージ内で最後に現れたtool_useブロックを返す。

    最初に得た（最新の）メッセージのtool_useのみ対象とし、ターン内で最後に出現したtool_useを使う。
    メッセージをまたいで探さない。tool_useが存在しない場合は`None`を返す。
    """
    for entry in reversed(entries):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        return next(
            (block for block in reversed(content) if isinstance(block, dict) and block.get("type") == "tool_use"),
            None,
        )
    return None


def tool_result_text_blocks(content: object) -> list[str]:
    """tool_result本文を文字列列へ正規化する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]


def extract_tool_result_id(message: dict) -> str | None:
    """userメッセージの`content`配列内の`tool_result`ブロックから`tool_use_id`を抽出する。"""
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id")
        if isinstance(tool_use_id, str):
            return tool_use_id
    return None
