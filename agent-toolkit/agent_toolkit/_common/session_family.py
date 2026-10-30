"""保存記録と子孫の再帰収集を、証拠抽出・費用CLI・ローカル画面・SSH先で共有する。

標準ライブラリと、標準ライブラリ・platformdirsだけで動く記録解決処理へ依存する。
各ホストの独立した履歴ディレクトリで同じ家族を求め、金額と証拠で対象を変えない。
"""

from __future__ import annotations

import datetime
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

from agent_toolkit._common import agents_server_tools as _agents_server_tool_names
from agent_toolkit._common import session_record_paths as _record_paths
from agent_toolkit._common.agy_records import is_agy_entry, normalize_entries

_Runtime = Literal["claude", "codex", "agy", "opencode"]


class _Record(NamedTuple):
    """transcriptの1エントリと、その由来行の1始まり行番号・原文。"""

    line: int
    text: str
    entry: dict[str, Any]

    @property
    def raw_entry(self) -> dict[str, Any]:
        """行の生の内容を、明示的な本文・構造照会へ返す。"""
        return json.loads(self.text)


class _RecordRecovery(NamedTuple):
    """JSONとして成立しない行を、同じ記録の後続の完全な行で置き換えた対応。

    `line`は成立しない行、`superseded_by`は同じ`ordinal`と`type`を持ち、成立しない行の`payload`以降を
    接頭辞として含む行である。どちらも1始まりの行番号で、原本は変更しない。
    """

    line: int
    superseded_by: int
    ordinal: int
    entry_type: str
    length: int


class _RecordLoadFailure(NamedTuple):
    """記録を読めなかった理由と、JSONが成立しない行の1始まりの行番号。"""

    reason: str
    line: int | None = None


class _RecordLoad(NamedTuple):
    """記録の読込結果。`records`が`None`なら`failure`が理由を持つ。"""

    records: list[_Record] | None
    recoveries: tuple[_RecordRecovery, ...] = ()
    failure: _RecordLoadFailure | None = None


class _CollectedRecord(NamedTuple):
    """全照会モードが共有する、由来と識別子を持つ1記録。"""

    record_id: str
    path: Path
    records: list[_Record]
    runtime: _Runtime | None
    source_record: str | None
    source_line: int | None
    agent_type: str | None
    role: Literal["main", "subagent", "session"]
    aliases: tuple[str, ...] = ()
    recoveries: tuple[_RecordRecovery, ...] = ()


class _UnresolvedRecord(NamedTuple):
    """解決できない委譲または委譲先記録を機械可読イベントへ渡す。"""

    record_id: str
    line: int
    kind: Literal["unresolved-record", "unresolved-delegation"] = "unresolved-record"
    reason: str | None = None


def _text_blocks(content: Any) -> list[str]:
    """Message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]


def _codex_text_blocks(content: Any) -> list[str]:
    """Codex message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"input_text", "output_text", "text"} and isinstance(block.get("text"), str):
            result.append(block["text"])
    return result


def _json_object(raw: Any) -> dict[str, Any] | None:
    """JSON文字列がobjectなら返し、破損または別の値なら`None`を返す。"""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


_AGY_EVENT_TYPES = frozenset({"init", "step_update", "result"})


def _detect_runtime(entries: list[dict[str, Any]]) -> _Runtime | None:
    """transcriptのエントリ形式から、記録を書いた実行系を判定して返す。"""
    if any(entry.get("type") == "agent_toolkit_input" or isinstance(entry.get("sessionID"), str) for entry in entries):
        return "opencode"
    if any(is_agy_entry(entry) for entry in entries):
        return "agy"
    entry_types = {entry.get("type") for entry in entries}
    if entry_types & {"response_item", "event_msg"}:
        return "codex"
    if entry_types & {"user", "assistant", "interrupt", "attachment", "queue-operation"} or any(
        entry.get("isSidechain") is True or "toolUseResult" in entry for entry in entries
    ):
        return "claude"
    return None


def _analysis_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """記録原本を保持し、会話以外のmessageの写しを解析から外す。"""
    if entry.get("type") not in {"user", "assistant"} and "message" in entry:
        return {key: value for key, value in entry.items() if key != "message"}
    return entry


_PARTIAL_HEAD = re.compile(rb'^\{"timestamp":"[^"]*","ordinal":(\d+),"type":"([^"]+)",')


def _superseding_line(broken: bytes, following: list[tuple[int, bytes]]) -> _RecordRecovery | None:
    """成立しない行が、直後の完全な行の途中までの書込みであれば対応を返す。

    Codexのrolloutでは、途中で終わった行（JSONの文字列やUTF-8の多バイト文字の途中で終わった行）の直後に、
    同じ`ordinal`の完全な行が書かれた記録を観測した。
    同じ`ordinal`と`type`を持ち、`payload`以降のバイト列が直後の行の接頭辞である場合に限って置き換え、
    それ以外の破損は回復せず読込失敗として扱う。
    """
    head = _PARTIAL_HEAD.match(broken)
    marker = broken.find(b'"payload":')
    if head is None or marker < 0:
        return None
    ordinal, entry_type, body = int(head.group(1)), head.group(2), broken[marker:]
    for number, line in following:
        if not line.strip():
            continue
        candidate = _PARTIAL_HEAD.match(line)
        start = line.find(b'"payload":')
        if candidate is None or int(candidate.group(1)) != ordinal or candidate.group(2) != entry_type:
            return None
        if start < 0 or not line[start:].startswith(body):
            return None
        try:
            json.loads(line.decode("utf-8"))
        except ValueError:
            return None
        return _RecordRecovery(0, number, ordinal, entry_type.decode("utf-8", "replace"), len(broken))
    return None


def _load_records_checked(raw_path: str | None) -> _RecordLoad:
    """絶対パスのJSONLを行番号付きで読み、回復した行と読めない理由を併せて返す。

    行は改行（LF）だけで分ける。JSONの文字列はU+2028などの区切り文字をそのまま含められるため、
    `str.splitlines`で分けると1つのレコードが複数行に分かれる。
    """
    if not raw_path:
        return _RecordLoad(None, failure=_RecordLoadFailure("記録のパスが指定されていない"))
    path = Path(raw_path)
    if not path.is_absolute():
        return _RecordLoad(None, failure=_RecordLoadFailure(f"記録のパスが絶対パスでない: {raw_path}"))
    try:
        data = path.read_bytes()
    except OSError as error:
        return _RecordLoad(None, failure=_RecordLoadFailure(f"記録を読み取れない: {error}"))

    lines = [(number, raw.removesuffix(b"\r")) for number, raw in enumerate(data.split(b"\n"), start=1)]
    records: list[_Record] = []
    recoveries: list[_RecordRecovery] = []
    for index, (number, raw) in enumerate(lines):
        if not raw.strip():
            continue
        try:
            line = raw.decode("utf-8")
            parsed = json.loads(line)
        except ValueError as error:
            recovery = _superseding_line(raw, lines[index + 1 :])
            if recovery is None:
                return _RecordLoad(None, failure=_RecordLoadFailure(f"{number}行目を読めない: {error}", number))
            recoveries.append(recovery._replace(line=number))
            continue
        if isinstance(parsed, dict):
            records.append(_Record(number, line, _analysis_entry(parsed)))
    normalized = normalize_entries([record.entry for record in records])
    return _RecordLoad(
        [record._replace(entry=entry) for record, entry in zip(records, normalized, strict=True)], tuple(recoveries)
    )


def _load_records(raw_path: str | None) -> list[_Record] | None:
    """絶対パスのJSONLを行番号付きで読み、失敗時は`None`を返す。"""
    return _load_records_checked(raw_path).records


def _parse_timestamp(value: str) -> datetime.datetime:
    """記録の`timestamp`などの内部データのISO 8601の時刻を解析し、タイムゾーン無しの値をUTCとして返す。

    ホストは記録の時刻を`Z`付きのUTCで書くため、タイムゾーン無しの値もUTCとみなす。
    CLI引数で受け取る時刻は`_parse_cli_timestamp`で解析する。
    """
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _record_timestamp(record: _Record) -> datetime.datetime | None:
    value = record.entry.get("timestamp")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.datetime.fromtimestamp(value / 1000, datetime.UTC)
    if not isinstance(value, str):
        return None
    try:
        return _parse_timestamp(value)
    except ValueError:
        return None


def _started_after_boundary(records: list[_Record], boundary: datetime.datetime) -> bool:
    """記録の最初の時刻が観測境界より後かを返す。"""
    timestamps = [_record_timestamp(record) for record in records]
    known = [timestamp for timestamp in timestamps if timestamp is not None]
    return bool(known) and min(known) > boundary


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
                if isinstance(call_id, str) and block.get("name") in {*_AGENTS_SERVER_TOOL_NAMES, "invoke_subagent"}:
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


def _codex_mcp_start_rejected(item: dict[str, Any]) -> bool:
    """agents_serverがエラー応答で拒否した起動項目かを返す。拒否された起動には委譲先が存在しない。"""
    result = item.get("result")
    return isinstance(result, dict) and result.get("isError") is True


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


def _session_path(
    session_id: str,
    codex_home: str | None = None,
    session_resolver: Callable[[str], tuple[Path, _Runtime] | None] | None = None,
) -> tuple[Path, _Runtime] | None:
    """委譲先の記録を`atk agents logs`と共有する解決処理で探し、記録と実行系を返す。

    Codexの記録が複数一致する場合は、別の委譲先の記録を混入させないよう解決できない扱いとする。
    """
    if session_resolver is not None:
        return session_resolver(session_id)
    found = _record_paths.find_session_record(session_id, codex_home=Path(codex_home) if codex_home else None)
    if found is None or found.engine not in _record_paths.RECORD_ENGINES:
        return None
    first, *others = found.paths
    if found.engine == "codex" and others:
        return None
    return first, cast(_Runtime, found.engine)


def _subagent_records(
    source: _CollectedRecord,
    unresolved: list[_UnresolvedRecord],
    modified_since: datetime.datetime | None = None,
) -> list[_CollectedRecord]:
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
        if modified_since is not None and path.stat().st_mtime < modified_since.timestamp():
            continue
        loaded = _load_records_checked(str(path))
        records = loaded.records
        if records is None:
            unresolved.append(
                _UnresolvedRecord(
                    f"{source.record_id}/{path.stem}", 0, reason=loaded.failure.reason if loaded.failure else None
                )
            )
            continue
        meta_path = path.with_name(f"{path.stem}.meta.json")
        try:
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError, ValueError:
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
                loaded.recoveries,
            )
        )
    return selected


def _collect_records(
    transcript_path: str,
    main_records: list[_Record],
    codex_home: str | None = None,
    boundary: datetime.datetime | None = None,
    *,
    main_only: bool = False,
    session_resolver: Callable[[str], tuple[Path, _Runtime] | None] | None = None,
    modified_since: datetime.datetime | None = None,
    main_recoveries: tuple[_RecordRecovery, ...] = (),
) -> tuple[list[_CollectedRecord], list[_UnresolvedRecord]]:
    """メイン記録から全ての付随記録と委譲先を発見順に再帰収集する。"""
    main_records = [
        record if (normalized := _analysis_entry(record.entry)) is record.entry else record._replace(entry=normalized)
        for record in main_records
    ]
    main_records = [
        record._replace(entry=entry)
        for record, entry in zip(main_records, normalize_entries([record.entry for record in main_records]), strict=True)
    ]
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
            main_recoveries,
        )
    ]
    if main_only:
        return collected, []
    seen_paths = {main_path.resolve()}
    seen_sessions: set[str] = set()
    unresolved: list[_UnresolvedRecord] = []
    index = 0
    while index < len(collected):
        source = collected[index]
        index += 1
        agents_server_call_ids = _agents_server_call_ids(source.records)
        unresolved_delegation_calls: set[str] = set()
        for subagent in _subagent_records(source, unresolved, modified_since):
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
            start_item = _codex_mcp_start_item(record.entry)
            if start_item is not None and not thread_ids and not _codex_mcp_start_rejected(start_item):
                unresolved.append(_UnresolvedRecord(source.record_id, record.line, "unresolved-delegation"))
            for session_id in thread_ids:
                if session_id in seen_sessions:
                    continue
                seen_sessions.add(session_id)
                resolved_session = _session_path(session_id, codex_home, session_resolver)
                if resolved_session is None:
                    unresolved.append(_UnresolvedRecord(session_id, record.line))
                    continue
                path, resolved_engine = resolved_session
                record_id = f"{resolved_engine}:{session_id}"
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                if modified_since is not None and path.stat().st_mtime < modified_since.timestamp():
                    continue
                loaded = _load_records_checked(str(path))
                records = loaded.records
                if records is None:
                    unresolved.append(
                        _UnresolvedRecord(session_id, record.line, reason=loaded.failure.reason if loaded.failure else None)
                    )
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
                        loaded.recoveries,
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
    """物理記録のmetadataから正規record IDを返す。"""
    if runtime == "codex":
        return f"codex:{_codex_thread_id_from_records(records) or path.stem.removeprefix('rollout-')}"
    if runtime == "claude":
        if path.parent.name == "subagents":
            return f"claude:{path.parent.parent.name}/{path.stem}"
        return f"claude:{path.stem}"
    if runtime == "agy" and path.name in {"transcript.jsonl", "transcript_full.jsonl"}:
        return f"agy:{path.parents[2].name}"
    if runtime in {"agy", "opencode"}:
        return f"{runtime}:{path.stem}"
    return f"record:{path.stem}"


def _codex_record_thread_id(item: _CollectedRecord) -> str | None:
    """Codex記録が属するthread IDを物理metadataから返す。"""
    if item.runtime != "codex":
        return None
    return _codex_thread_id_from_records(item.records)
