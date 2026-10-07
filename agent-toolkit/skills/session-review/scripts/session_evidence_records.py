"""証拠抽出の対象記録の解決と、メイン記録に付随する記録（サブエージェント、委譲先）の再帰的な収集。

記録の保存先の探索は`agent_toolkit._agents_server.record_paths`へ委ね、本モジュールは委譲の痕跡から識別子を取り出して収集の順序を決める。
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, cast

from session_evidence_extract import (
    RUNTIME_EXTRACTORS,
    _codex_text_blocks,
    _CollectedRecord,
    _detect_runtime,
    _json_object,
    _load_records,
    _Record,
    _record_timestamp,
    _Runtime,
    _started_after_boundary,
    _text_blocks,
    _UnresolvedRecord,
)

from agent_toolkit._agents_server import record_paths as _record_paths
from agent_toolkit._agents_server import tool_names as _agents_server_tool_names

_THREAD_ID_KEYS = ("session_id", "sessionId", "threadId", "conversationId")


# 新しい委譲記録の発見元は子sessionを生成する起動ツールに限る。統合前に保存された記録の旧名も含める。
# 既存session操作と外側実行セルの入力文字列は、新しい委譲の証拠にならない。
_AGENTS_SERVER_TOOL_NAMES = frozenset(
    f"{namespace}{name}"
    for namespace in _agents_server_tool_names.MCP_NAMESPACES
    for name in _agents_server_tool_names.RECORDED_START_OPERATIONS
)


_TASK_RESULT_PATTERN = re.compile(r"<task-notification\b[^>]*>.*?<result>\s*(.*?)\s*</result>", re.DOTALL)


def _thread_id_from_mapping(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in _THREAD_ID_KEYS:
        thread = value.get(key)
        if isinstance(thread, str) and thread:
            return thread
    structured = value.get("structuredContent")
    if isinstance(structured, dict):
        for key in _THREAD_ID_KEYS:
            thread = structured.get(key)
            if isinstance(thread, str) and thread:
                return thread
    return None


def _agents_server_call_ids(records: list[_Record]) -> set[str]:
    """ClaudeとCodexの直接呼び出し形式にある起動ツールの呼び出しIDを返す。"""
    call_ids: set[str] = set()
    for record in records:
        message = record.entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                call_id = block.get("id")
                if isinstance(call_id, str) and block.get("name") in _AGENTS_SERVER_TOOL_NAMES:
                    call_ids.add(call_id)

        payload = record.entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") not in {"custom_tool_call", "function_call"}:
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            continue
        name = payload.get("name")
        if name in _AGENTS_SERVER_TOOL_NAMES:
            call_ids.add(call_id)
    return call_ids


def _codex_mcp_start_item(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Codexのexec経由で完了したagents_server起動項目を返す。"""
    payload = entry.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    if (
        entry.get("type") == "event_msg"
        and isinstance(payload, dict)
        and payload.get("type") == "item_completed"
        and isinstance(item, dict)
        and item.get("type") == "McpToolCall"
        and item.get("server") == "agents_server"
        and isinstance(item.get("tool"), str)
        and item.get("tool") in _agents_server_tool_names.RECORDED_START_OPERATIONS
    ):
        return item
    return None


def _thread_ids_from_record(
    record: _Record,
    agents_server_call_ids: set[str],
) -> list[str]:
    """Claude transcriptとCodex rolloutから委譲先のsession識別子を抽出する。

    識別子は実行系をまたいで一意であるため、呼び出しに現れる実行系の指定は取り出さない。
    """
    entry = record.entry
    found: list[str] = list(_native_agent_thread_ids(entry))

    def add_mapping(value: Any) -> None:
        mapping = value if isinstance(value, dict) else _json_object(value)
        if not isinstance(mapping, dict):
            return
        session_id = _thread_id_from_mapping(mapping)
        if session_id:
            found.append(session_id)

    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    result_call_ids = (
        {
            block.get("tool_use_id")
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
        }
        if isinstance(content, list)
        else set()
    )

    if result_call_ids & agents_server_call_ids:
        mcp_meta = entry.get("mcpMeta")
        if isinstance(mcp_meta, dict):
            add_mapping(mcp_meta.get("structuredContent"))

        add_mapping(entry.get("toolUseResult"))

    payload = entry.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "custom_tool_call_output" and payload.get("call_id") in agents_server_call_ids:
            output = payload.get("output")
            add_mapping(output)
            for text in _codex_text_blocks(output):
                add_mapping(text)
        if payload.get("type") == "function_call_output" and payload.get("call_id") in agents_server_call_ids:
            output = payload.get("output")
            add_mapping(output)
            for text in _codex_text_blocks(output):
                add_mapping(text)
        if item := _codex_mcp_start_item(entry):
            add_mapping(item.get("result"))

    notification_texts: list[str] = []
    if entry.get("type") == "queue-operation":
        notification_texts.extend(_text_blocks(entry.get("content")))
    notification_texts.extend(_text_blocks(content))
    for text in notification_texts:
        for match in _TASK_RESULT_PATTERN.finditer(text):
            add_mapping(match.group(1))
    return list(dict.fromkeys(found))


def _delegation_output_call_id(record: _Record, agents_server_call_ids: set[str]) -> str | None:
    """agents_server起動に対応するCodexの出力レコードから呼び出しIDを返す。"""
    payload = record.entry.get("payload")
    if not isinstance(payload, dict) or payload.get("type") not in {"custom_tool_call_output", "function_call_output"}:
        return None
    call_id = payload.get("call_id")
    return call_id if isinstance(call_id, str) and call_id in agents_server_call_ids else None


def _native_agent_thread_ids(value: Any) -> list[str]:
    """Codexの`SubAgentActivity.agent_thread_id`を構造化フィールドから再帰取得する。"""
    found: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "SubAgentActivity":
            thread_id = value.get("agent_thread_id")
            if isinstance(thread_id, str) and thread_id:
                found.append(thread_id)
        for item in value.values():
            found.extend(_native_agent_thread_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_native_agent_thread_ids(item))
    return list(dict.fromkeys(found))


def _resolve_claude_transcript(session_id: str) -> Path:
    """Claude Codeのセッション識別子から親transcriptを1件解決する。

    委譲先の記録の解決と同じ探索を使い、Claude Codeの`projects`配下にある親セッションの記録だけを受理する。
    """
    found = _record_paths.find_session_record(session_id)
    if found is None or found.engine != "claude":
        raise ValueError(f"対象記録を解決できない: Claude Codeのセッション識別子{session_id}に一致する記録が無い")
    first, *others = found.paths
    if others or first.parent.name == "subagents":
        joined = ", ".join(str(path) for path in found.paths)
        raise ValueError(
            f"対象記録を解決できない: Claude Codeのセッション識別子{session_id}が親セッションの記録1件に一致しない: {joined}"
        )
    return first


def _session_path(session_id: str, codex_home: str | None = None) -> tuple[Path, _Runtime] | None:
    """委譲先の記録を`atk agents logs`と共有する解決処理で探し、記録と実行系を返す。

    Codexの記録が複数一致する場合は、別の委譲先の記録を混入させないよう解決できない扱いとする。
    """
    found = _record_paths.find_session_record(session_id, codex_home=Path(codex_home) if codex_home else None)
    if found is None or found.engine not in RUNTIME_EXTRACTORS:
        return None
    first, *others = found.paths
    if found.engine == "codex" and others:
        return None
    return first, cast(_Runtime, found.engine)


def _subagent_records(source: _CollectedRecord) -> list[_CollectedRecord]:
    """Claude記録に付随するサブエージェント記録をファイル名順で返す。"""
    if source.runtime != "claude":
        return []
    subagent_dir = source.path.with_suffix("") / "subagents"
    try:
        paths = sorted(subagent_dir.glob("agent-*.jsonl"))
    except OSError:
        return []
    selected: list[_CollectedRecord] = []
    for path in paths:
        records = _load_records(str(path))
        if records is None:
            continue
        meta_path = path.with_name(f"{path.stem}.meta.json")
        try:
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            raw_meta = {}
        agent_type = raw_meta.get("agentType") if isinstance(raw_meta, dict) else None
        record_id = f"{source.record_id}/{path.stem}"
        legacy_id = path.stem if source.role == "main" else record_id
        selected.append(
            _CollectedRecord(
                record_id,
                path,
                records,
                _detect_runtime([record.entry for record in records]),
                source.record_id,
                None,
                agent_type if isinstance(agent_type, str) else None,
                "subagent",
                (legacy_id,) if legacy_id != record_id else (),
            )
        )
    return selected


def _collect_records(
    transcript_path: str,
    main_records: list[_Record],
    codex_home: str | None = None,
    boundary: datetime.datetime | None = None,
) -> tuple[list[_CollectedRecord], list[_UnresolvedRecord]]:
    """メイン記録から全ての付随記録と委譲先を発見順に再帰収集する。"""
    main_path = Path(transcript_path)
    main_runtime = _detect_runtime([record.entry for record in main_records])
    main_id = _physical_record_id(main_path, main_records, main_runtime)
    collected = [
        _CollectedRecord(
            main_id,
            main_path,
            main_records,
            main_runtime,
            None,
            None,
            None,
            "main",
            ("main",),
        )
    ]
    seen_paths = {main_path.resolve()}
    seen_sessions: set[str] = set()
    unresolved: list[_UnresolvedRecord] = []
    index = 0
    while index < len(collected):
        source = collected[index]
        index += 1
        agents_server_call_ids = _agents_server_call_ids(source.records)
        unresolved_delegation_calls: set[str] = set()
        for subagent in _subagent_records(source):
            if boundary is not None and _started_after_boundary(subagent.records, boundary):
                continue
            resolved = subagent.path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            collected.append(subagent)
        for record in source.records:
            if boundary is not None and (timestamp := _record_timestamp(record)) is not None and timestamp > boundary:
                continue
            thread_ids = _thread_ids_from_record(record, agents_server_call_ids)
            call_id = _delegation_output_call_id(record, agents_server_call_ids)
            if call_id and not thread_ids and call_id not in unresolved_delegation_calls:
                unresolved.append(_UnresolvedRecord(source.record_id, record.line, "unresolved-delegation"))
                unresolved_delegation_calls.add(call_id)
            if _codex_mcp_start_item(record.entry) is not None and not thread_ids:
                unresolved.append(_UnresolvedRecord(source.record_id, record.line, "unresolved-delegation"))
            for session_id in thread_ids:
                if session_id in seen_sessions:
                    continue
                seen_sessions.add(session_id)
                resolved_session = _session_path(session_id, codex_home)
                if resolved_session is None:
                    unresolved.append(_UnresolvedRecord(session_id, record.line))
                    continue
                path, resolved_engine = resolved_session
                record_id = f"{resolved_engine}:{session_id}"
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                records = _load_records(str(path))
                if records is None:
                    unresolved.append(_UnresolvedRecord(session_id, record.line))
                    continue
                seen_paths.add(resolved)
                collected.append(
                    _CollectedRecord(
                        record_id,
                        path,
                        records,
                        resolved_engine,
                        source.record_id,
                        record.line,
                        None,
                        "session",
                        (),
                    )
                )
    return collected, unresolved


def _codex_thread_id_from_records(records: list[_Record]) -> str | None:
    """Codex記録の物理metadataからthread IDを返す。"""
    for record in records:
        entry = record.entry
        payload = entry.get("payload")
        if entry.get("type") == "session_meta" and isinstance(payload, dict):
            thread_id = payload.get("id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def _physical_record_id(path: Path, records: list[_Record], runtime: _Runtime | None) -> str:
    """収集元の指定ではなく物理記録のmetadataから正規record IDを返す。"""
    if runtime == "codex":
        return f"codex:{_codex_thread_id_from_records(records) or path.stem.removeprefix('rollout-')}"
    if runtime == "claude":
        if path.parent.name == "subagents":
            return f"claude:{path.parent.parent.name}/{path.stem}"
        return f"claude:{path.stem}"
    if runtime == "agy":
        return f"agy:{path.stem}"
    return f"record:{path.stem}"


def _codex_record_thread_id(item: _CollectedRecord) -> str | None:
    """Codex記録が属するthread IDを物理metadataから返す。"""
    if item.runtime != "codex":
        return None
    return _codex_thread_id_from_records(item.records)
