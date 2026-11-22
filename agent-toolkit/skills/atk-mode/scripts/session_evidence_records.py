from agent_toolkit._common.session_family import (
    _agents_server_call_ids,
    _codex_mcp_start_item,
    _codex_record_thread_id,
    _codex_thread_id_from_records,
    _CollectedRecord,
    _delegation_output_call_id,
    _load_records_checked,
    _native_agent_thread_ids,
    _physical_record_id,
    _Record,
    _resolve_claude_transcript,
    _session_path,
    _subagent_records,
    _thread_id_from_mapping,
    _thread_ids_from_record,
)
from agent_toolkit._common.session_family import (
    _collect_records as _collect_family_records,
)

__all__ = [
    "_thread_id_from_mapping",
    "_agents_server_call_ids",
    "_codex_mcp_start_item",
    "_thread_ids_from_record",
    "_delegation_output_call_id",
    "_native_agent_thread_ids",
    "_resolve_claude_transcript",
    "_session_path",
    "_subagent_records",
    "_collect_records",
    "_codex_thread_id_from_records",
    "_physical_record_id",
    "_codex_record_thread_id",
]


def _fork_parent(records: list[_Record]) -> str | None:
    for record in records:
        if record.entry.get("type") == "session_meta":
            payload = record.entry.get("payload")
            if isinstance(payload, dict) and isinstance(parent := payload.get("forked_from_id"), str):
                return parent or None
    return None


def _fork_context(records: list[_Record], parent: list[_Record] | None) -> tuple[tuple[_Record, ...], bool]:
    child_rows = [row for row in records if row.entry.get("type") != "session_meta"]
    if parent is None:
        return tuple(child_rows), False
    parent_rows = (row for row in parent if row.entry.get("type") != "session_meta")
    matched = []
    for child, original in zip(child_rows, parent_rows, strict=False):
        if (child.entry.get("type"), child.entry.get("payload")) != (original.entry.get("type"), original.entry.get("payload")):
            break
        matched.append(child)

    return (tuple(matched), True) if matched else (tuple(child_rows), False)


def _collect_records(transcript_path, main_records, codex_home=None, boundary=None, **options):
    collected, unresolved = _collect_family_records(transcript_path, main_records, codex_home, boundary, **options)
    by_id = {item.record_id: item.records for item in collected}
    loader = options.get("record_loader", _load_records_checked)
    resolver = options.get("session_resolver")
    result: list[_CollectedRecord] = []
    for item in collected:
        parent = _fork_parent(item.records) if item.runtime == "codex" else None
        if parent is None:
            result.append(item)
            continue
        parent_rows = by_id.get(f"codex:{parent}")
        if parent_rows is None:
            resolved = _session_path(parent, codex_home, resolver)
            if resolved is not None and resolved[1] == "codex" and resolved[0] != item.path:
                parent_rows = loader(str(resolved[0])).records
        context, known = _fork_context(item.records, parent_rows)
        excluded = {row.line for row in context}
        result.append(
            item._replace(
                records=[row for row in item.records if row.line not in excluded],
                fork_context=context,
                fork_parent=parent,
                fork_attribution_known=known,
            )
        )
    return result, unresolved
