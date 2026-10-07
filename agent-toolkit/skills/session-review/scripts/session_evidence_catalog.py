"""証拠抽出のカタログ走査（`--catalog-claude-project`・`--catalog-codex-history`）。複数の記録からWI処理と委譲の痕跡を一覧にする。"""

from __future__ import annotations

import collections
import datetime
import re
from pathlib import Path
from typing import Any, NamedTuple

from session_evidence_extract import (
    _CATALOG_ROOT_NEXT_ACTION,
    _ENV_ASSIGNMENT,
    _basename,
    _CollectedRecord,
    _detect_runtime,
    _error_event,
    _extract_records,
    _json_object,
    _load_records,
    _parse_timestamp,
    _payload_command_tokens,
    _Record,
    _Runtime,
    _shell_tokens,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_records import (
    _agents_server_call_ids,
    _codex_record_thread_id,
    _collect_records,
    _thread_ids_from_record,
)
from session_evidence_stats import (
    _add_tokens,
    _stats_summary_data,
)
from session_evidence_tool_calls import (
    _sorted_tool_call_events,
    _tool_call_events,
    _tool_call_summary,
)

from agent_toolkit._agents_server import record_paths as _record_paths


def _catalog_session_id(path: Path, records: list[_Record], runtime: _Runtime) -> str:
    """カタログ内記録のセッション識別子を返す。"""
    if runtime == "claude":
        return path.stem
    item = _CollectedRecord("main", path, records, runtime, None, None, None, "main")
    return _codex_record_thread_id(item) or path.stem.removeprefix("rollout-")


def _catalog_value(records: list[_Record], *keys: str) -> str:
    """セッションmetadataの先頭の非空文字列を返す。"""
    for record in records:
        entry = record.entry
        payload = entry.get("payload")
        for source in (entry, payload if isinstance(payload, dict) else {}):
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
        if isinstance(payload, dict):
            git = payload.get("git")
            if isinstance(git, dict):
                for key in keys:
                    value = git.get(key)
                    if isinstance(value, str) and value:
                        return value
    return "unknown"


def _wi_command(tokens: list[str]) -> str | None:
    """直接実行された`atk wi`の操作名を返す。"""
    index = 0
    while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
        index += 1
    words = tokens[index:]
    if len(words) < 3 or _basename(words[0]) != "atk" or words[1] != "wi":
        return None
    return words[2]


def _successful_wi_operations(item: _CollectedRecord) -> list[dict[str, Any]]:
    """成功結果まで記録された直接の`atk wi`操作を返す。"""
    pending: dict[str, tuple[int, str]] = {}
    operations: list[dict[str, Any]] = []
    for record in item.records:
        entry = record.entry
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    block_input = block.get("input")
                    command = block_input.get("command") if isinstance(block_input, dict) else None
                    operation = _wi_command(_shell_tokens(command)) if isinstance(command, str) else None
                    if operation is not None:
                        pending[block["id"]] = (record.line, operation)
                if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    call = pending.pop(block["tool_use_id"], None)
                    if call is not None and block.get("is_error") is not True:
                        line, operation = call
                        operations.append({"operation": operation, "locator": {"record": item.record_id, "line": line}})

        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if payload_type in {"function_call", "custom_tool_call"} and isinstance(payload.get("call_id"), str):
            operation = _wi_command(_payload_command_tokens(payload))
            if operation is not None:
                pending[payload["call_id"]] = (record.line, operation)
        elif payload_type in {"function_call_output", "custom_tool_call_output"} and isinstance(payload.get("call_id"), str):
            call = pending.pop(payload["call_id"], None)
            output = payload.get("output")
            output_object = output if isinstance(output, dict) else _json_object(output)
            failed = isinstance(output_object, dict) and output_object.get("exit_code") not in (None, 0)
            if call is not None and not failed:
                line, operation = call
                operations.append({"operation": operation, "locator": {"record": item.record_id, "line": line}})
        elif entry.get("type") == "event_msg" and payload_type == "item_completed":
            command_item = payload.get("item")
            if not isinstance(command_item, dict) or command_item.get("type") != "CommandExecution":
                continue
            operation = _wi_command(_payload_command_tokens(payload))
            if operation is not None and command_item.get("status") in {"completed", "success", "succeeded"}:
                operations.append({"operation": operation, "locator": {"record": item.record_id, "line": record.line}})
    return operations


def _workflow_evidence(item: _CollectedRecord) -> tuple[str, dict[str, Any] | str]:
    """明示されたprocess-wi系workflowと位置を返す。"""
    for event in _extract_records(item.records):
        text = event.get("text")
        if event.get("kind") == "skill-invocation" and isinstance(text, str) and "process-wi" in text:
            return "process-wi", {"record": item.record_id, "line": int(event["line"])}
        if (
            event.get("kind") == "user"
            and isinstance(text, str)
            and ("agent-toolkit:process-wi" in text or text.strip() == "/process-wi")
        ):
            return "process-wi", {"record": item.record_id, "line": int(event["line"])}
    return "unknown", "unknown"


def _catalog_family_tokens(family: list[_CollectedRecord]) -> dict[str, int] | str:
    """親とroot内で追跡できた子孫のトークンを同じ成分ごとに合算する。"""
    total: dict[str, int] = {}
    found = False
    for item in family:
        if item.runtime is None:
            continue
        tokens = _stats_summary_data(item.records, item.runtime).get("tokens")
        if not isinstance(tokens, dict):
            continue
        found = True
        _add_tokens(total, {key: value for key, value in tokens.items() if isinstance(value, int)})
    return total if found else "unknown"


def _catalog_agy_child(session_id: str) -> _CollectedRecord | None:
    """走査rootの外にあるAntigravityの委譲先ログを、カタログの子として読み込む。

    agyの委譲先の記録はClaude CodeとCodexの履歴ディレクトリに置かれないため、走査rootだけからは子を引けない。
    ClaudeとCodexの子は指定root内だけの比較一覧という前提を保つため、走査rootの記録からだけ引く。
    """
    found = _record_paths.find_session_record(session_id)
    if found is None or found.engine != "agy":
        return None
    records = _load_records(str(found.paths[0]))
    if records is None:
        return None
    return _CollectedRecord(session_id, found.paths[0], records, "agy", None, None, None, "session")


class _CatalogParent(NamedTuple):
    """カタログの窓の中の親セッションと、走査rootの中で組み立てた子孫を含む記録群。"""

    item: _CollectedRecord
    family: list[_CollectedRecord]
    start_text: str | None
    end_text: str | None


class _CatalogScan(NamedTuple):
    """カタログ走査の対象範囲と、窓の中の親セッション。"""

    root: Path
    parents: list[_CatalogParent]
    unresolved_count: int


def _catalog_scan(
    root: Path,
    runtime: _Runtime,
    since: datetime.datetime,
    boundary: datetime.datetime,
) -> _CatalogScan | dict[str, Any]:
    """指定root内だけから窓の中の親セッションを選ぶ。rootから記録を判別できない場合はエラーイベントを返す。"""
    if not root.is_dir():
        return _error_event(f"カタログrootが実在するディレクトリでない: {root}", next_action=_CATALOG_ROOT_NEXT_ACTION)
    resolved_root = root.resolve()
    paths = sorted(resolved_root.glob("**/*.jsonl"))
    if runtime == "codex":
        paths = [path for path in paths if path.name.startswith("rollout-")]
    if not paths:
        return _error_event(
            f"カタログrootから{runtime}記録を判別できない: {resolved_root}", next_action=_CATALOG_ROOT_NEXT_ACTION
        )
    loaded: dict[str, _CollectedRecord] = {}
    path_items: dict[Path, _CollectedRecord] = {}
    unresolved_loads = 0
    for path in paths:
        records = _load_records(str(path))
        if records is None:
            unresolved_loads += 1
            continue
        detected = _detect_runtime([record.entry for record in records])
        if detected != runtime:
            continue
        session_id = _catalog_session_id(path, records, runtime)
        item = _CollectedRecord(session_id, path, records, runtime, None, None, None, "main")
        loaded.setdefault(session_id, item)
        path_items[path.resolve()] = item
    if not loaded:
        return _error_event(
            f"カタログrootから{runtime}記録を判別できない: {resolved_root}", next_action=_CATALOG_ROOT_NEXT_ACTION
        )

    children: dict[str, list[str]] = {session_id: [] for session_id in loaded}
    referenced: set[str] = set()
    unresolved_references: set[tuple[str, str]] = set()
    for session_id, item in list(loaded.items()):
        call_ids = _agents_server_call_ids(item.records)
        for record in item.records:
            for child_id in _thread_ids_from_record(record, call_ids):
                if child_id not in loaded and (agy_child := _catalog_agy_child(child_id)) is not None:
                    loaded[child_id] = agy_child
                    children[child_id] = []
                if child_id in loaded:
                    if child_id not in children[session_id]:
                        children[session_id].append(child_id)
                    referenced.add(child_id)
                else:
                    unresolved_references.add((session_id, child_id))
        if runtime == "claude":
            subagent_dir = item.path.with_suffix("") / "subagents"
            for path, child in path_items.items():
                if path.parent == subagent_dir.resolve() and child.record_id != session_id:
                    if child.record_id not in children[session_id]:
                        children[session_id].append(child.record_id)
                    referenced.add(child.record_id)

    if runtime == "claude":
        parent_ids = [
            item.record_id
            for path, item in path_items.items()
            if path.parent == resolved_root and item.record_id not in referenced
        ]
    else:
        parent_ids = [session_id for session_id in loaded if session_id not in referenced]

    parents: list[_CatalogParent] = []
    for parent_id in sorted(set(parent_ids)):
        parent = loaded[parent_id]
        stats = _stats_summary_data(parent.records, runtime)
        start_text = stats.get("start")
        end_text = stats.get("end")
        start = _parse_timestamp(start_text) if isinstance(start_text, str) else None
        end = _parse_timestamp(end_text) if isinstance(end_text, str) else None
        if end is not None and end <= since:
            continue
        if start is not None and start > boundary:
            continue
        descendant_ids: list[str] = []
        queue = list(children[parent_id])
        while queue:
            child_id = queue.pop(0)
            if child_id in descendant_ids:
                continue
            descendant_ids.append(child_id)
            queue.extend(children.get(child_id, ()))
        parents.append(
            _CatalogParent(
                parent,
                [parent, *(loaded[child_id] for child_id in descendant_ids)],
                start_text if isinstance(start_text, str) else None,
                end_text if isinstance(end_text, str) else None,
            )
        )
    return _CatalogScan(resolved_root, parents, unresolved_loads + len(unresolved_references))


def _catalog_events(
    root: Path,
    runtime: _Runtime,
    since: datetime.datetime,
    boundary: datetime.datetime,
) -> tuple[list[dict[str, Any]], int]:
    """指定root内だけから比較用の親セッションカタログを生成する。"""
    scan = _catalog_scan(root, runtime, since, boundary)
    if isinstance(scan, dict):
        return [scan], 2
    events: list[dict[str, Any]] = []
    for parent in scan.parents:
        workflow, workflow_locator = _workflow_evidence(parent.item)
        operations = [operation for item in parent.family for operation in _successful_wi_operations(item)]
        events.append(
            {
                "kind": "catalog-parent",
                "runtime": runtime,
                "session_id": parent.item.record_id,
                "started_at": parent.start_text or "unknown",
                "finished_at": parent.end_text or "unknown",
                "cwd": _catalog_value(parent.item.records, "cwd", "originalCwd"),
                "branch": _catalog_value(parent.item.records, "gitBranch", "originalBranch", "branch"),
                "workflow": workflow,
                "workflow_locator": workflow_locator,
                "tokens": _catalog_family_tokens(parent.family),
                "descendant_count": len(parent.family) - 1,
                "successful_wi_operations": operations,
                "successful_wi_operation_count": len(operations),
            }
        )
    events.sort(key=lambda event: (str(event["started_at"]), str(event["session_id"])))
    events.append(
        {
            "kind": "catalog-summary",
            "scan_root": str(scan.root),
            "runtime": runtime,
            "since": since.isoformat(),
            "observation_boundary": boundary.isoformat(),
            "parent_record_count": len(events),
            "unresolved_record_count": scan.unresolved_count,
        }
    )
    return events, 0


def _catalog_tool_call_events(
    root: Path,
    runtime: _Runtime,
    since: datetime.datetime,
    boundary: datetime.datetime,
    tools: list[str] | None,
    pattern: re.Pattern[str] | None,
) -> tuple[list[dict[str, Any]], int]:
    """カタログの窓の中の親セッションごとに、委譲先を含む記録の窓の中のツール呼び出しを返す。

    委譲先は親の記録を起点として`_collect_records`で集める。カタログが走査rootの中だけで組み立てる子孫を使うと、
    rootの外（別のプロジェクトディレクトリなど）に置かれた委譲先の呼び出しが数えられないためである。
    窓は`timestamp`が`since`より後で`boundary`以前の呼び出しとし、時刻を持たない呼び出しは含めない。
    """
    scan = _catalog_scan(root, runtime, since, boundary)
    if isinstance(scan, dict):
        return [scan], 2
    events: list[dict[str, Any]] = []
    unresolved: list[_UnresolvedRecord] = []
    for parent in scan.parents:
        collected, parent_unresolved = _collect_records(str(parent.item.path), parent.item.records, None, boundary)
        unresolved.extend(parent_unresolved)
        for item in collected:
            for event in _tool_call_events(item, tools, pattern):
                try:
                    timestamp = _parse_timestamp(event["timestamp"]) if isinstance(event["timestamp"], str) else None
                except ValueError:
                    timestamp = None
                if timestamp is not None and since < timestamp <= boundary:
                    events.append({**event, "session_id": parent.item.record_id})
    events = _sorted_tool_call_events(events)
    summary = _tool_call_summary(events)
    summary["by_session"] = dict(sorted(collections.Counter(str(event["session_id"]) for event in events).items()))
    summary["scan_root"] = str(scan.root)
    summary["since"] = since.isoformat()
    summary["observation_boundary"] = boundary.isoformat()
    summary["parent_record_count"] = len(scan.parents)
    return [*events, *_unresolved_events(unresolved), summary], 0
