"""証拠抽出の`--grep`・`--detail`照会。記録の本文の検索と、行番号を指定した記録1行の詳細を返す。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from session_evidence_extract import (
    _BODY_KEYS,
    _LINE_NUMBER_PREFIX,
    _METADATA_KEYS,
    _RECORD_LOCATOR_NEXT_ACTION,
    _clip,
    _codex_text_blocks,
    _CollectedRecord,
    _DetailBudget,
    _error_event,
    _events_with_record,
    _Record,
    _scannable_records,
    _text_blocks,
    _unresolved_events,
    _UnresolvedRecord,
)

from agent_toolkit._common import transcript as _transcript

_MAX_DETAIL_LENGTH = 8000


_PERSISTED_OUTPUT_PREFIX = "<persisted-output>"


def _grep_events(records: list[_Record], pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    """エントリ内の全本文から一致行を集め、一致したエントリ数の要約を末尾へ付ける。"""
    events: list[dict[str, Any]] = []
    matched = 0
    for record in _scannable_records(records):
        matched_lines = _matched_lines(record.entry, pattern)
        events.extend(
            {"kind": "match", "line": record.line, "timestamp": _entry_timestamp(record.entry), "text": _clip(line_text)}
            for line_text in matched_lines
        )
        matched += 1 if matched_lines else 0
    events.append({"kind": "summary", "count": matched})
    return events


def _detail_events(records: list[_Record], numbers: list[int]) -> tuple[list[dict[str, Any]], int]:
    """指定行のエントリを整形して返す。範囲外の行番号はエラーと終了コード2を返す。"""
    index = {record.line: record.entry for record in records}
    events: list[dict[str, Any]] = []
    for number in numbers:
        entry = index.get(number)
        if entry is None:
            return [_error_event(f"行番号{number}は範囲外", next_action=_RECORD_LOCATOR_NEXT_ACTION)], 2
        events.extend(_entry_detail_events(number, entry, full_message_text=True))
    return events, 0


def _entry_detail_events(
    line: int, entry: dict[str, Any], *, limit: int = _MAX_DETAIL_LENGTH, full_message_text: bool = False
) -> list[dict[str, Any]]:
    """1エントリの詳細を、tool_use・tool_resultのブロック単位で整形する。

    クリップの上限はエントリ全体で共有し、ブロックの出現順に予算を配分する。
    予算超過で省略が生じたエントリは、そのエントリの全イベントへ`omitted`を付ける。
    予算が尽きた後の本文は空文字列となるため、この標識が無ければ
    空の出力が元から空だったのか省略の結果なのかを判別できない。
    各イベントは元エントリの`timestamp`を持ち、区間境界の時刻を元記録を読み直さずに確定できるようにする。

    `full_message_text`では、ユーザーとアシスタントの発話本文（送信本文を含む）を予算の外で切り詰めずに先頭へ返す。
    会話の流れは長い発話の先頭と末尾だけを載せて記録位置を添えるため、その位置の照会で全文へ到達できる必要がある。
    """
    budget = _DetailBudget(limit)
    timestamp = _entry_timestamp(entry)
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    events: list[dict[str, Any]] = []
    if full_message_text:
        events.extend(
            {"kind": "detail", "line": line, "timestamp": timestamp, "role": role, "text": text}
            for role, text in _message_texts(entry)
        )
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                if full_message_text and entry.get("type") == "assistant" and _transcript.visible_text_blocks([block]):
                    continue
                events.append(
                    {
                        "kind": "detail",
                        "line": line,
                        "timestamp": timestamp,
                        "name": str(block.get("name", "")),
                        "input": _clip_structure(block.get("input"), budget),
                    }
                )
            elif block.get("type") == "tool_result":
                events.append(
                    {
                        "kind": "detail",
                        "line": line,
                        "timestamp": timestamp,
                        "tool": str(block.get("tool_use_id", "")),
                        "text": budget.clip(_tool_result_body(block, entry)),
                    }
                )
    if not events:
        events = [
            {
                "kind": "detail",
                "line": line,
                "timestamp": timestamp,
                "text": budget.clip(json.dumps(entry, ensure_ascii=False, indent=2)),
            }
        ]
    if budget.omitted:
        for event in events:
            event["omitted"] = True
    return events


def _message_texts(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """役割と可視本文を返す。送信ツールの入力はassistantの本文にだけ含める。"""
    message = entry.get("message")
    if (
        isinstance(message, dict)
        and entry.get("type") in {"user", "assistant"}
        and message.get("role") in {"user", "assistant"}
    ):
        content = message.get("content")
        texts = (
            _transcript.visible_text_blocks(content)
            if entry.get("type") == "assistant" and message.get("role") == "assistant" and isinstance(content, list)
            else _text_blocks(content)
        )
        return [(str(message["role"]), text) for text in texts if text.strip()]
    payload = entry.get("payload")
    if (
        entry.get("type") == "response_item"
        and isinstance(payload, dict)
        and payload.get("type") == "message"
        and payload.get("role") in {"user", "assistant"}
    ):
        return [(str(payload["role"]), text) for text in _codex_text_blocks(payload.get("content")) if text.strip()]
    return []


def _entry_timestamp(entry: dict[str, Any]) -> str | None:
    """元記録行の時刻を返す。時刻を持たないエントリは`None`とする。"""
    timestamp = entry.get("timestamp")
    return timestamp if isinstance(timestamp, str) else None


def _persisted_result(result: Any) -> Any:
    """退避した実行結果の`stdout`を、保存先のファイルの内容へ置き換えた写しを返す。

    agent-toolkitのPostToolUseは、退避したシェル出力の`stdout`を未読の通知へ置き換え、元の出力は
    `persistedOutputPath`のファイルだけが持つ。保存先が実在するファイルならその内容を実体とし、
    無い場合（回収済みなど）は記録の値をそのまま返す。
    """
    if not isinstance(result, dict):
        return result
    path = result.get("persistedOutputPath")
    if not isinstance(path, str) or not path.strip():
        return result
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    return {**result, "stdout": content}


def _tool_result_body(block: dict[str, Any], entry: dict[str, Any]) -> str:
    """tool_resultブロックの実体本文を取得する。

    大きなツール出力は退避先ファイルへ移され、message側のcontentには退避通知だけが残る。
    この形態ではmessage側から実際の出力を取得できないため、
    同エントリのツール実行結果が持つ標準出力・標準エラー（保存先が実在すればその内容）を本文とする。
    """
    body = "\n".join(_text_blocks(block.get("content")))
    if body.strip() and not body.lstrip().startswith(_PERSISTED_OUTPUT_PREFIX):
        return body
    result = _persisted_result(entry.get("toolUseResult"))
    if isinstance(result, str):
        return result or body
    if not isinstance(result, dict):
        return body
    streams = [value for key in ("stdout", "stderr") if isinstance(value := result.get(key), str) and value.strip()]
    return "\n".join(streams) or body


def _clip_structure(value: Any, budget: _DetailBudget) -> Any:
    """入力の構造を保ったまま、文字列だけをエントリ共有の予算で制限する。"""
    if isinstance(value, str):
        return budget.clip(value)
    if isinstance(value, dict):
        return {key: _clip_structure(item, budget) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_structure(item, budget) for item in value]
    return value


def _entry_texts(entry: dict[str, Any]) -> list[str]:
    """runtimeを問わず、1エントリの検索対象テキストを出現順に取得する。

    エントリの構造を再帰的にたどり、文字列値をすべて集める。
    メッセージ本文・tool_use入力・tool_result本文・ツール実行結果の生出力に加え、
    hook通知が入る`attachment`配下のような未知のフィールドも対象となる。
    既知フィールドを列挙する方式は、通知の格納先が増えるたびに新しい格納先が検索対象にならないため採らない。
    `_METADATA_KEYS`の値は本文を持たない管理用の値（識別子・時刻・形式名・実行環境）であり、
    走査しても一致を増やすだけとなるため除外する。
    除外の可否は深さではなく、値を保持するフィールドの構造上の役割で判定する。
    エントリ・`message`・`payload`・`item`・各ブロックのようなプロトコル構造は、
    深さを問わず区分値と識別子を保持するため除外の対象とする。
    `input`・`output`・`arguments`・`prompt`のような自由形式の本文フィールドでは、
    `mode`・`status`のような汎用語のキーがユーザーの入力そのものを保持するため、
    その内部のキーを除外しない。
    """
    texts: list[str] = []
    _collect_texts(entry, texts)
    # 退避した実行結果の実体は保存先のファイルだけが持つため、記録の値に加えてその内容も検索の対象にする。
    result = entry.get("toolUseResult")
    persisted = _persisted_result(result)
    if persisted is not result:
        _collect_texts(persisted.get("stdout"), texts)
    return texts


def _collect_texts(value: Any, texts: list[str], *, in_body: bool = False) -> None:
    """構造をたどり、プロトコル構造が持つ管理用フィールドを除く文字列値を`texts`へ追加する。

    `in_body`は、自由形式の本文を保持するフィールドの内部を走査中であることを表す。
    """
    if isinstance(value, str):
        if value.strip():
            texts.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not in_body and key in _METADATA_KEYS:
                continue
            _collect_texts(item, texts, in_body=in_body or key in _BODY_KEYS)
    elif isinstance(value, list):
        for item in value:
            _collect_texts(item, texts, in_body=in_body)


def _matched_lines(entry: dict[str, Any], pattern: re.Pattern[str]) -> list[str]:
    """エントリ内で一致した行を、同一本文の重複を除いて出現順に返す。

    退避された実行結果と可視テキストのように、同一の本文が複数のフィールドへ重複して格納される場合がある。
    また、退避出力だけへ付く行番号接頭辞を別本文の番号なし行と比較する場合がある。
    そのため、別本文に同じ番号なし行がある行番号付き行だけを本文の重複として扱い、表示は最初に現れた原文を保つ。
    """
    texts = _entry_texts(entry)
    unnumbered_lines_by_text = [
        {line_text.strip() for line_text in text.splitlines() if _LINE_NUMBER_PREFIX.match(line_text) is None} for text in texts
    ]
    seen: set[str] = set()
    matched: list[str] = []
    for index, text in enumerate(texts):
        for line_text in text.splitlines():
            stripped = line_text.strip()
            if not pattern.search(line_text):
                continue
            key = stripped
            numbered = _LINE_NUMBER_PREFIX.match(line_text)
            if numbered and any(
                other_index != index and numbered.group(1).strip() in other_lines
                for other_index, other_lines in enumerate(unnumbered_lines_by_text)
            ):
                key = numbered.group(1).strip()
            if key in seen:
                continue
            seen.add(key)
            matched.append(line_text)
    return matched


def _tool_hint(entry: dict[str, Any]) -> str | None:
    """エントリに含まれるコマンド先頭行またはtool_use_idを取得する。"""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str) and command.strip():
                    return _clip(command.splitlines()[0])
                if isinstance(block.get("id"), str):
                    return block["id"]
            if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                return block["tool_use_id"]

    payload = entry.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    command = item.get("command") if isinstance(item, dict) else None
    if isinstance(command, list) and command:
        text = " ".join(part for part in command if isinstance(part, str)).strip()
        return _clip(text.splitlines()[0]) if text else None
    return None


def _grep_collection_events(
    collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord], pattern: re.Pattern[str]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    total = 0
    for item in collected:
        record_events = _grep_events(item.records, pattern)
        record_count = record_events[-1]["count"]
        total += record_count
        events.extend(_events_with_record(record_events[:-1], item.record_id))
        if record_count:
            events.extend(_events_with_record(record_events[-1:], item.record_id))
    events.append({"kind": "summary", "count": total})
    events.extend(_unresolved_events(unresolved))
    return events


def _fixed_string_collection_events(
    collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord], phrases: list[str]
) -> list[dict[str, Any]]:
    """固定文字列ごとに一致entry数とlocatorだけを返す。"""
    events: list[dict[str, Any]] = []
    for phrase in phrases:
        locators: list[dict[str, Any]] = []
        pattern = re.compile(re.escape(phrase))
        for item in collected:
            for record in _scannable_records(item.records):
                if not _matched_lines(record.entry, pattern):
                    continue
                locators.append(
                    {
                        "record": item.record_id,
                        "line": record.line,
                        "timestamp": _entry_timestamp(record.entry),
                    }
                )
        events.append({"kind": "fixed-string-summary", "query": phrase, "count": len(locators), "locators": locators})
    events.extend(_unresolved_events(unresolved))
    return events


def _resolve_record_alias(collected: list[_CollectedRecord], record_id: str) -> tuple[_CollectedRecord | None, bool]:
    """正規IDまたは一意な旧IDを解決し、旧IDが曖昧なら印を返す。"""
    canonical = [item for item in collected if item.record_id == record_id]
    if canonical:
        return canonical[0], False
    matches = [item for item in collected if record_id in item.aliases]
    if len(matches) == 1:
        return matches[0], False
    return None, len(matches) > 1


def _resolve_record_locator(
    collected: list[_CollectedRecord], locator: str, *, label: str
) -> tuple[_CollectedRecord | None, int | None, list[dict[str, Any]] | None]:
    """公開locatorを正規IDと行番号へ解決する。"""
    if ":" in locator:
        record_id, raw_line = locator.rsplit(":", 1)
    else:
        record_id, raw_line = "main", locator
    if not record_id or not raw_line.isdecimal():
        return None, None, [_error_event(f"{label}位置が不正: {locator}", next_action=_RECORD_LOCATOR_NEXT_ACTION)]
    selected, ambiguous = _resolve_record_alias(collected, record_id)
    if ambiguous:
        return None, None, [_error_event(f"記録別名が曖昧: {record_id}", next_action=_RECORD_LOCATOR_NEXT_ACTION)]
    if selected is None:
        return None, None, [_error_event(f"記録が不明: {record_id}", next_action=_RECORD_LOCATOR_NEXT_ACTION)]
    line = int(raw_line)
    if not any(record.line == line for record in selected.records):
        return None, None, [_error_event(f"行番号{line}は範囲外", next_action=_RECORD_LOCATOR_NEXT_ACTION)]
    return selected, line, None


def _detail_collection_events(collected: list[_CollectedRecord], locators: list[str]) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    for locator in locators:
        selected, line, error_events = _resolve_record_locator(collected, locator, label="詳細")
        if error_events is not None:
            return error_events, 2
        assert selected is not None and line is not None
        record_events, exit_code = _detail_events(selected.records, [line])
        if exit_code:
            return record_events, exit_code
        events.extend(_events_with_record(record_events, selected.record_id))
    return events, 0
