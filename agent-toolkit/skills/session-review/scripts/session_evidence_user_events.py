"""証拠抽出の`--user-events`照会と会話の流れ。人間の発話と確認回答を逐語引用の原文として返す。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from session_evidence_extract import (
    _TEXT_LIMIT,
    _claude_entry_events,
    _codex_entry_events,
    _CodexPendingQuestions,
    _CollectedRecord,
    _detect_runtime,
    _events_with_record,
    _extract_records,
    _finalize,
    _generated_user_event,
    _is_subagent_record,
    _PendingQuestion,
    _record_timestamp,
)
from session_evidence_tool_calls import (
    _conversation_tool_calls,
)


def _saved_user_events_at(path: Path, position: str) -> list[dict[str, Any]]:
    """保存済みイベントを元記録の欄の組で選ぶ。一意性と由来の判断は消費側に残す。"""
    record, separator, raw_line = position.rpartition(":")
    if not separator or not record or not raw_line.isascii() or not raw_line.isdecimal() or int(raw_line) < 1:
        raise ValueError(f"記録位置が不正: {position}（record全体と正の整数lineをコロンで区切る）")
    if not path.is_absolute():
        raise ValueError(f"保存済み出力には絶対パスを指定する: {path}")
    line = int(raw_line)
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for physical_line, text in enumerate(stream, start=1):
            if not text.strip():
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f"JSONLが不正: {path}の物理行{physical_line}: {error.msg}") from error
            if not isinstance(event, dict):
                raise ValueError(f"JSONLのイベントがオブジェクトでない: {path}の物理行{physical_line}")
            if event.get("record") == record and event.get("line") == line:
                events.append(event)
    if not events:
        raise ValueError(f"保存済み出力に一致する記録位置がない: {path}の{position}")
    return events


def _user_events_since(collected: list[_CollectedRecord], since: datetime.datetime | None) -> list[dict[str, Any]]:
    """メイン記録の状態を保ち、指定時刻より後に成立したユーザーイベントだけを返す。

    `since`が`None`の場合はメイン記録の最初から返す。
    出力は逐語引用と文字列比較する原文として使うため、本文を切り詰めない。
    """
    token = _TEXT_LIMIT.set(None)
    try:
        return _collect_user_events_since(collected, since)
    finally:
        _TEXT_LIMIT.reset(token)


_CLAUDE_INTERRUPT_MARKERS = frozenset({"[Request interrupted by user]", "[Request interrupted by user for tool use]"})
"""Claude Codeが中断時にユーザーロールへ書く定型文。本文全体がこの定型文の記録だけが該当する。

人間が書いた本文ではないため逐語引用の出所から除く。会話の流れと介入候補では、ツール実行の中断として
振り返りの判定対象に残すため、共通の生成本文判定には加えず`--user-events`の抽出だけで除く。
"""


def _collect_user_events_since(collected: list[_CollectedRecord], since: datetime.datetime | None) -> list[dict[str, Any]]:
    """`_user_events_since`の抽出本体。"""
    events: list[dict[str, Any]] = []
    for item in collected:
        if item.role != "main":
            continue
        runtime = _detect_runtime([record.entry for record in item.records])
        if runtime is None:
            break
        selected_events: list[dict[str, Any]] = []
        pending_claude_questions: dict[str, _PendingQuestion] = {}
        pending_questions: _CodexPendingQuestions = {}
        subagent_record = _is_subagent_record([record.entry for record in item.records])
        for record in item.records:
            record_events = (
                _codex_entry_events(record.entry, record.line, pending_questions)
                if runtime == "codex"
                else _claude_entry_events(record.entry, record.line, pending_claude_questions, subagent_record)
            )
            for event in record_events:
                event.setdefault("line", record.line)
            timestamp = _record_timestamp(record)
            if since is None or (timestamp is not None and timestamp > since):
                selected_events.extend(record_events)
        user_events = [
            event
            for event in _finalize(selected_events)
            if event["kind"] == "user"
            and not _generated_user_event(event)
            and not (runtime == "claude" and event["text"] in _CLAUDE_INTERRUPT_MARKERS)
        ]
        events.extend(_events_with_record(user_events, item.record_id))
        break
    events.append({"kind": "summary", "count": len(events)})
    return events


def _conversation_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """メイン記録のユーザー発話とアシスタント発話、ツール呼び出しおよび失敗の標識を時系列で返す。

    振り返りでセッション全体の流れ（試したコマンド、読み書きしたファイル、委譲の起動、遠回り、手戻り、
    同じ論点の反復、ユーザーによる是正）を通読するための入力とする。発話は切り詰めずに返す。
    ツール呼び出しはツール名と代表入力だけを返し、書き込む本文と置換文字列は含めない。
    ツール結果は本スクリプトが`failed-tool`として検出した失敗だけを、診断の1行とともに返す。
    自動挿入本文、実行環境が生成した本文、スキル本文の展開、hookの追加コンテキスト、成功したツール結果の本文は除く。
    確認への回答（本文は回答値と自由記述、質問と選択肢は`assistant_context`）と初期要求はユーザーの入力として残す。
    委譲先の内部は問題候補の側で扱うため、メイン記録だけを対象とする。
    """
    main_record = next((item for item in collected if item.role == "main"), None)
    if main_record is None:
        return []
    token = _TEXT_LIMIT.set(None)
    try:
        events = _extract_records(main_record.records)
    finally:
        _TEXT_LIMIT.reset(token)
    # 同じ記録行の中では発話を先に、ツール呼び出しを内容の順に、失敗の標識を最後に並べる。
    ordered: list[tuple[int, int, dict[str, Any]]] = []
    for event in events:
        kind = event.get("kind")
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        raw_line = event.get("line")
        line = raw_line if isinstance(raw_line, int) else 0
        if kind == "failed-tool":
            diagnostic = event.get("diagnostic_last_line") or next(
                (value.strip() for value in reversed(text.splitlines()) if value.strip()), ""
            )
            ordered.append(
                (
                    line,
                    2,
                    {
                        "kind": "tool-failure",
                        "record": "main",
                        "line": event.get("line"),
                        "timestamp": event.get("timestamp"),
                        "call_id": event.get("tool"),
                        "text": diagnostic,
                    },
                )
            )
            continue
        if kind == "user":
            if _generated_user_event(event):
                continue
            role = "user"
        elif kind in {"assistant", "final-result"}:
            role = "assistant"
        else:
            continue
        ordered.append(
            (
                line,
                0,
                {
                    "kind": "utterance",
                    "role": role,
                    "record": "main",
                    "line": event.get("line"),
                    "timestamp": event.get("timestamp"),
                    "text": text,
                },
            )
        )
    ordered.extend((call["line"], 1, call) for call in _conversation_tool_calls(main_record.records))
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [item for _, _, item in ordered]
