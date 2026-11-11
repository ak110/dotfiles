"""振り返りの証拠抽出の本体。Claude Code・Codex・Antigravityの記録を時系列イベントへ変換し、照会モードが共有する値と処理を持つ。

`session_review_evidence.py`（`atk run-script session-review-evidence`の起動スクリプト）と同じディレクトリに置き、
各照会モードのモジュールが本モジュールの記録の読込、イベントの組み立て、本文の切り詰めと自身の起動の判定を使う。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from session_evidence_extract_claude import _claude_entry_events, _extract_claude, _is_subagent_record
from session_evidence_extract_codex import _codex_entry_events, _extract_codex
from session_evidence_extract_commands import (
    _RECORD_LOCATOR_NEXT_ACTION,
    _basename,
    _payload_command_tokens,
    _result_call_ids,
    _scannable_records,
    _shell_tokens,
)
from session_evidence_extract_common import (
    _BODY_KEYS,
    _CANDIDATE_VARIABLE,
    _CANDIDATE_VARIABLE_PLACEHOLDER,
    _ENV_ASSIGNMENT,
    _FALLBACK_TEXT,
    _HOOK_FAILURE_PREFIX,
    _HOOK_NOTICE_MARKER,
    _LINE_NUMBER_PREFIX,
    _METADATA_KEYS,
    _OMISSION_MARK,
    _SHELL_NAMES,
    _TEXT_LIMIT,
    _clip,
    _DetailBudget,
    _event,
    _generated_user_event,
    _is_hook_record,
)
from session_evidence_extract_questions import _CodexPendingQuestions, _PendingQuestion
from session_evidence_extract_runtimes import _extract_agy, _extract_opencode, _opencode_records

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._common import agy_records as _agy_records
from agent_toolkit._common.runtime_inserted import is_runtime_generated as _is_runtime_generated
from agent_toolkit._common.runtime_inserted import is_runtime_inserted_text as _is_runtime_inserted_text
from agent_toolkit._common.session_family import (
    _codex_text_blocks,
    _CollectedRecord,
    _detect_runtime,
    _json_object,
    _load_records,
    _load_records_checked,
    _parse_timestamp,
    _Record,
    _record_timestamp,
    _Runtime,
    _started_after_boundary,
    _text_blocks,
    _UnresolvedRecord,
)

RUNTIME_EXTRACTORS: dict[str, Callable[[list[dict[str, Any]], list[int]], list[dict[str, Any]]]] = {
    "claude": _extract_claude,
    "codex": _extract_codex,
    "agy": _extract_agy,
    "opencode": _extract_opencode,
}
"""実行系ごとの時系列への変換。キーの集合は本スクリプトが変換できる実行系を表す。"""


def _finalize(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1turnの最終結果への置換と連番付けを行う。

    Claude Codeのサブエージェントは報告本文を`SubagentHandback`の引数で渡し、その後に定型文だけを書く。
    同呼び出しを持つturnでは、最後の同呼び出しの本文を最終結果とする。
    """
    handbacks = [event for event in events if event.get("handback")]
    if handbacks:
        handbacks[-1]["kind"] = "final-result"
    else:
        for event in reversed(events):
            if event["kind"] == "assistant" and event.get("phase") != "commentary":
                event["kind"] = "final-result"
                break
    for event in events:
        event.pop("handback", None)
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def extract(entries: list[dict[str, Any]], lines: list[int] | None = None) -> list[dict[str, Any]]:
    """transcript形式を判定し、セッション全体の対象イベントを順序どおり抽出する。

    `lines`はエントリごとのtranscript行番号。省略時はエントリの並び順を行番号とみなす。
    """
    if not entries:
        return []
    entries = _agy_records.normalize_entries(entries)
    runtime = _detect_runtime(entries)
    if runtime is None:
        return _fallback()
    numbers = lines if lines is not None else list(range(1, len(entries) + 1))
    events: list[dict[str, Any]] = []
    start = 0
    for index, entry in enumerate(entries):
        if _turn_finished(entry, runtime):
            events.extend(_finalize(_extract_for_runtime(entries[start : index + 1], runtime, numbers[start : index + 1])))
            start = index + 1
    if start < len(entries):
        events.extend(_finalize(_extract_for_runtime(entries[start:], runtime, numbers[start:])))
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def _turn_finished(entry: dict[str, Any], runtime: _Runtime) -> bool:
    """既存のホストの終了記録を使い、後続turnの作業を先行返却の分類から切り離す。"""
    if runtime == "opencode":
        if entry.get("_opencode_sqlite") is True:
            message = entry.get("message")
            return (
                entry.get("_opencode_error") is True or isinstance(message, dict) and message.get("stop_reason") == "end_turn"
            )
        part = entry.get("part")
        return (
            entry.get("type") == "error"
            or entry.get("type") == "step_finish"
            and isinstance(part, dict)
            and part.get("reason") == "stop"
        )
    if runtime == "codex":
        payload = entry.get("payload")
        return entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_complete"
    if runtime == "claude":
        message = entry.get("message")
        return entry.get("type") == "assistant" and isinstance(message, dict) and message.get("stop_reason") == "end_turn"
    return entry.get("_agy_original", entry).get("event") == "result"


def _extract_for_runtime(
    entries: list[dict[str, Any]],
    runtime: _Runtime,
    lines: list[int] | None = None,
) -> list[dict[str, Any]]:
    """確定したruntimeに対応する共通イベントへ変換する。"""
    numbers = lines if lines is not None else list(range(1, len(entries) + 1))
    return RUNTIME_EXTRACTORS[runtime](entries, numbers)


def _fallback() -> list[dict[str, Any]]:
    return [{"sequence": 1, "kind": "fallback", "text": _FALLBACK_TEXT}]


def load_and_extract(raw_path: str) -> list[dict[str, Any]]:
    """絶対パスのJSONLを一度読み、抽出結果を返す。"""
    loaded = _load_records_checked(raw_path)
    if loaded.records is None:
        reason = f"（{loaded.failure.reason}）" if loaded.failure is not None else ""
        raise ValueError(f"対象記録を読み込めない: {raw_path}{reason}")
    return _extract_records(loaded.records)


def _extract_records(records: list[_Record]) -> list[dict[str, Any]]:
    """読み込み済みレコードから行番号付きの時系列イベントを取得する。"""
    return extract([record.entry for record in records], [record.line for record in records])


def _unresolved_events(unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    """解決できなかった委譲先を機械可読イベントへ変換する。"""
    return [
        {
            "kind": item.kind,
            "record": item.record_id,
            "line": item.line,
            **({"reason": item.reason} if item.reason else {}),
            **({"failure_line": item.failure_line} if item.failure_line is not None else {}),
            "next_action": (
                (
                    f"記録 {item.record_id}:{item.line} の委譲入力と結果を確認し、"
                    "委譲先の識別子と記録の所在を記録保持者へ問い合わせる。取得できない場合は未確認範囲を報告する。"
                )
                if item.kind == "unresolved-delegation"
                else (
                    f"保持元ホストで記録 {item.record_id} の所在と読取権限を確認する。"
                    "failure_lineがあればその行付近の形式も確認する。"
                    "取得できなければ記録保持者へ参照位置と失敗理由を示して原記録を依頼し、未確認範囲を報告する。"
                )
            ),
        }
        for item in unresolved
    ]


def _recovery_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """JSONとして成立しない行を後続の完全な行で置き換えた対応を、記録と行番号で示す。"""
    return [
        {
            "kind": "recovered-record-line",
            "record": item.record_id,
            "line": recovery.line,
            "superseded_by": recovery.superseded_by,
            "ordinal": recovery.ordinal,
            "entry_type": recovery.entry_type,
            "length": recovery.length,
        }
        for item in collected
        for recovery in item.recoveries
    ]


def _role_document(item: _CollectedRecord) -> str | None:
    """最初の配送本文の役割文書を読み、圧縮後の再掲で起動時の役割を上書きしない。"""
    for record in item.records:
        message = record.entry.get("message")
        payload = record.entry.get("payload")
        texts: list[str] = []
        if record.entry.get("type") == "agent_toolkit_input" and isinstance(record.entry.get("prompt"), str):
            texts = [record.entry["prompt"]]
        elif record.entry.get("type") == "user" and isinstance(message, dict):
            texts = _text_blocks(message.get("content"))
        elif isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") == "user":
            texts = _codex_text_blocks(payload.get("content"))
        for text in texts:
            found = re.search(r"(?m)^次の文書の手順を実行せよ（出所: [^\n]*[/\\]([^/\\\n]+)\.subagent\.md）。", text)
            if found:
                return found[1]
        if texts and not _is_runtime_generated(record.entry) and not all(_is_runtime_inserted_text(text) for text in texts):
            return None
    return None


def _launch_mode(item: _CollectedRecord, collected: list[_CollectedRecord]) -> str | None:
    """起動結果と対応する親の呼び出し入力からmodeを得る。"""
    if item.role == "subagent":
        return item.agent_type
    parent = next((source for source in collected if source.record_id == item.source_record), None)
    if parent is None or item.source_line is None:
        return None
    source = next((record.entry for record in parent.records if record.line == item.source_line), {})
    payload = source.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("item"), dict):
        call = payload["item"]
        arguments = call.get("arguments")
        if isinstance(arguments, dict):
            return _agents_server_tool_names.start_mode(str(call.get("tool", "")), arguments)
    ids: set[str] = set()
    message = source.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        ids = {
            str(block["tool_use_id"]) for block in message["content"] if isinstance(block, dict) and block.get("tool_use_id")
        }
    if isinstance(payload, dict) and payload.get("call_id"):
        ids.add(str(payload["call_id"]))
    for record in parent.records:
        message = record.entry.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") in ids:
                    inputs = block.get("input")
                    if isinstance(inputs, dict):
                        return _agents_server_tool_names.start_mode(str(block.get("name", "")).rsplit("__", 1)[-1], inputs)
        call = record.entry.get("payload")
        if isinstance(call, dict) and call.get("call_id") in ids and call.get("type") in {"function_call", "custom_tool_call"}:
            arguments = call.get("arguments")
            inputs = arguments if isinstance(arguments, dict) else _json_object(arguments)
            if inputs is not None:
                return _agents_server_tool_names.start_mode(str(call.get("name", "")).rsplit("__", 1)[-1], inputs)
    return None


def _collection_events(
    collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord], *, recoveries: bool = True
) -> list[dict[str, Any]]:
    """全収集照会が同じ記録由来と未解決の対象、回復した記録行を出力する。"""
    return [
        *(
            {
                "kind": "record-provenance",
                "record": item.record_id,
                "role": item.role,
                "source_record": item.source_record,
                "source_line": item.source_line,
                "mode": _launch_mode(item, collected),
                "role_document": _role_document(item),
            }
            for item in collected
        ),
        *_unresolved_events(unresolved),
        *(_recovery_events(collected) if recoveries else []),
    ]


def _referenced_provenance_events(collected: list[_CollectedRecord], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """照会結果が参照する記録と、その`source_record`を根までたどった起動元の記録の`record-provenance`だけを返す。

    参照は、イベントの`record`欄と、イベントが持つ配列（`locators`・`samples`など）の要素の`record`欄とする。
    全収集記録の由来を前置すると、委譲先の多い記録では由来の行数が結果を大きく上回り、由来の行の中から結果を探しにくくなるためである。
    全記録の由来は通常表示と`--bundle`が書くファイルが保つ。
    """
    referenced: set[str] = set()
    for event in events:
        values: list[Any] = [event.get("record")]
        for value in event.values():
            if isinstance(value, list):
                values.extend(item.get("record") for item in value if isinstance(item, dict))
        referenced.update(value for value in values if isinstance(value, str))
    sources = {item.record_id: item.source_record for item in collected}
    pending = list(referenced)
    while pending:
        source = sources.get(pending.pop())
        if source is not None and source not in referenced:
            referenced.add(source)
            pending.append(source)
    return [event for event in _collection_events(collected, [], recoveries=False) if event["record"] in referenced]


_CATALOG_ROOT_NEXT_ACTION = (
    "Claude Codeは`--catalog-claude-project`へ`~/.claude/projects/<プロジェクト>`の絶対パスを、"
    "Codexは`--catalog-codex-history`へCodexの記録ディレクトリの絶対パスを渡して再実行する"
)


_SINCE_NEXT_ACTION = (
    "`--since`へISO 8601形式の時刻（例: `2026-09-30T00:00:00+09:00`。"
    "タイムゾーンを省くと実行ホストのローカル時刻として扱う）を渡して再実行する"
)


_BOUNDARY_NEXT_ACTION = (
    "`--observation-boundary`へ`--since`以後のISO 8601形式の時刻（例: `2026-09-30T12:00:00+09:00`。"
    "タイムゾーンを省くと実行ホストのローカル時刻として扱う）を渡して再実行する"
)


_ELAPSED_UNTIL_NEXT_ACTION = (
    "`--elapsed-until`へ記録の最初のレコード以後の時刻をISO 8601形式（例: `2026-09-30T12:00:00+09:00`。"
    "タイムゾーンを省くと実行ホストのローカル時刻として扱う）で渡すか、時刻を持つ記録を対象にして再実行する"
)


def _error_event(text: str, *, next_action: str) -> dict[str, Any]:
    """照会不能を示すエラーイベントを、次の操作の項目`next_action`付きで返す。"""
    if not next_action.strip():
        raise ValueError("エラーイベントのnext_actionは空文字列以外で指定する必要がある")
    return {"kind": "error", "text": text, "next_action": next_action}


def _events_with_record(events: list[dict[str, Any]], record_id: str) -> list[dict[str, Any]]:
    """イベントを複製し、由来記録IDを付ける。"""
    return [{**event, "record": record_id} for event in events]


def _default_events(collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        events.extend(_events_with_record(_extract_records(item.records), item.record_id))
    events.extend(_collection_events(collected, unresolved))
    return events


__all__ = [
    "_BODY_KEYS",
    "_CANDIDATE_VARIABLE",
    "_CANDIDATE_VARIABLE_PLACEHOLDER",
    "_CodexPendingQuestions",
    "_CollectedRecord",
    "_DetailBudget",
    "_ENV_ASSIGNMENT",
    "_HOOK_FAILURE_PREFIX",
    "_HOOK_NOTICE_MARKER",
    "_LINE_NUMBER_PREFIX",
    "_METADATA_KEYS",
    "_OMISSION_MARK",
    "_PendingQuestion",
    "_RECORD_LOCATOR_NEXT_ACTION",
    "_Record",
    "_Runtime",
    "_SHELL_NAMES",
    "_TEXT_LIMIT",
    "_UnresolvedRecord",
    "_basename",
    "_claude_entry_events",
    "_clip",
    "_codex_entry_events",
    "_codex_text_blocks",
    "_detect_runtime",
    "_event",
    "_generated_user_event",
    "_is_hook_record",
    "_is_subagent_record",
    "_json_object",
    "_load_records",
    "_load_records_checked",
    "_opencode_records",
    "_parse_timestamp",
    "_payload_command_tokens",
    "_record_timestamp",
    "_result_call_ids",
    "_scannable_records",
    "_shell_tokens",
    "_started_after_boundary",
    "_text_blocks",
    "extract",
    "load_and_extract",
]
