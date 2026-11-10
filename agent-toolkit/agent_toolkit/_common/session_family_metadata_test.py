"""通常読込と本文なし索引のJSONL回復・参照抽出の境界を比較する。"""

import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from agent_toolkit._common import session_family as family
from agent_toolkit._common.session_family import (
    _agents_server_call_ids,
    _collect_records,
    _load_records_checked,
    _scan_record_metadata,
    _thread_ids_from_record,
)
from agent_toolkit._testing.session_evidence_support import (
    agy_step,
    codex_tool_result_entry,
    codex_tool_use_entry,
    write_jsonl,
)


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("damage", [None, b"not-json", b"\xff", "partial-json", "partial-utf8"])
def test_metadata_matches_checked_jsonl_boundary(tmp_path: Path, ending: bytes, damage: bytes | str | None) -> None:
    """空行・非object・U+2028・部分書込みと後半破損で物理行と読込成否をそろえる。"""
    path = tmp_path / "rollout-test.jsonl"
    raw = json.dumps(
        {
            "timestamp": "2026-10-01T00:00:00Z",
            "ordinal": 7,
            "type": "response_item",
            "payload": {"text": "日本語\u2028本文"},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    lines = [b"", b"42", raw]
    if isinstance(damage, bytes):
        lines.append(damage)
    elif damage:
        partial = raw[: raw.index("本".encode()) + 1] if damage == "partial-utf8" else raw[:-4]
        lines.extend([partial, b"", raw])
    path.write_bytes(ending.join(lines) + ending)
    loaded = _load_records_checked(str(path))
    meta = _scan_record_metadata(path)
    assert meta.failure == loaded.failure
    if loaded.records is None:
        assert meta.record_count == 0
        assert not meta.child_ids
        assert meta.runtime is None
        return
    assert meta.record_count == len(loaded.records)
    assert meta.recoveries == loaded.recoveries
    assert [record.line for record in loaded.records] == ([3, 6] if damage else [3])
    assert meta.runtime == "codex"
    assert meta.start == datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
    assert meta.start_value == meta.end_value == "2026-10-01T00:00:00Z"
    if damage:
        assert meta.recoveries[0].line == 4 and meta.recoveries[0].superseded_by == 6


@pytest.mark.parametrize("runtime", ["claude", "codex", "agy"])
def test_metadata_extracts_references_without_body_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime: str
) -> None:
    """結果が呼出より前でも対応させ、本文ローダー・全量バイト読込へ依存しない。"""
    path = tmp_path / "record.jsonl"
    timestamp = "2026-10-01T00:00:00Z"
    if runtime == "claude":
        entries = [
            codex_tool_result_entry(timestamp, "start", "child"),
            codex_tool_use_entry(timestamp, "start", "child"),
        ]
    elif runtime == "codex":
        entries = [
            {"type": "session_meta", "payload": {"id": "physical", "source": {"subagent": {}}}},
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "start", "output": '{"session_id":"child"}'},
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "mcp__agents_server__start", "call_id": "start"},
            },
            {"type": "event_msg", "payload": {"type": "SubAgentActivity", "agent_thread_id": "native-child"}},
        ]
    else:
        entries = [
            agy_step(
                1,
                "PLANNER_RESPONSE",
                "DONE",
                tool_calls=[{"name": "invoke_subagent", "id": "start", "args": {"prompt": "大きな本文"}}],
            ),
            agy_step(2, "GENERIC", "DONE", content='{"session_id":"child"}'),
        ]
    write_jsonl(path, entries)
    loaded = _load_records_checked(str(path))
    assert loaded.records is not None
    calls = _agents_server_call_ids(loaded.records)
    expected = tuple(dict.fromkeys(child for record in loaded.records for child in _thread_ids_from_record(record, calls)))

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("メタ索引が全量本文の読込を使用した")

    monkeypatch.setattr(family, "_load_records_checked", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    meta = _scan_record_metadata(path)
    assert meta.failure is None
    assert meta.runtime == runtime
    assert meta.child_ids == expected
    assert "child" in meta.child_ids
    assert meta.is_subagent == (runtime == "codex")
    assert not any(isinstance(value, (dict, list)) for value in meta)


def test_metadata_discards_ids_before_late_failure(tmp_path: Path) -> None:
    """正常なIDと子参照の後で破損したファイルを、部分的な関係索引として採用しない。"""
    path = tmp_path / "rollout-broken.jsonl"
    write_jsonl(
        path,
        [
            {"type": "session_meta", "payload": {"id": "parent"}},
            {"type": "event_msg", "payload": {"type": "SubAgentActivity", "agent_thread_id": "child"}},
        ],
    )
    with path.open("ab") as stream:
        stream.write(b"\xff\n")
    meta = _scan_record_metadata(path)
    assert meta.failure is not None and meta.failure.line == 3
    assert not meta.record_id and not meta.child_ids
    assert meta.start is None and meta.record_count == 0


@pytest.mark.parametrize("mode", ["subagent", "session"])
def test_family_preserves_failure_line_separately_from_reference(tmp_path: Path, mode: str) -> None:
    """家族の読込失敗は参照元位置と失敗した記録の物理行を分けて保持する。"""
    parent = tmp_path / "parent.jsonl"
    timestamp = "2026-10-01T00:00:00Z"
    entries = [codex_tool_use_entry(timestamp, "start", "child"), codex_tool_result_entry(timestamp, "start", "child")]
    if mode == "subagent":
        entries = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
        child = parent.with_suffix("") / "subagents/agent-child.jsonl"
    else:
        child = tmp_path / "child.jsonl"
    write_jsonl(parent, entries)
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_bytes(b"{}\n{broken}\n")
    loaded = _load_records_checked(str(parent))
    assert loaded.records is not None

    def resolve(_session_id: str, **_kwargs: Any) -> tuple[Path, family._Runtime]:
        return child, "claude"

    collected, unresolved = _collect_records(str(parent), loaded.records, session_resolver=resolve)
    assert len(collected) == 1
    assert len(unresolved) == 1
    failure = unresolved[0]
    assert failure.line == (0 if mode == "subagent" else 2)
    assert failure.failure_line == 2
    assert failure.reason and "2行目" in failure.reason
