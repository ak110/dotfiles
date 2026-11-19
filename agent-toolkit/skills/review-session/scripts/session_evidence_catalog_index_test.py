"""カタログ走査が、保存した記録ごとの値から同じ結果を返し、記録の書き換え後は新しい親子関係で返すことを公開CLIで確かめる。"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.session_evidence_support import (
    codex_item,
    codex_tool_result_entry,
    codex_tool_use_entry,
    read_jsonl,
    timestamped_entry,
    write_jsonl,
)

_PARENT = "11111111-1111-4111-8111-111111111111"
_CHILD = "22222222-2222-4222-8222-222222222222"
_TIMESTAMP = "2026-10-06T00:00:00Z"


def _record(root: pathlib.Path, runtime: str, identity: str, child: str | None = None) -> pathlib.Path:
    """`child`を指定すると、その会話をagents_serverで起動した記録にする。"""
    if runtime == "claude":
        path = root / f"{identity}.jsonl"
        entries = [timestamped_entry(_TIMESTAMP, identity)]
        if child:
            entries.extend(
                [codex_tool_use_entry(_TIMESTAMP, "start", child), codex_tool_result_entry(_TIMESTAMP, "start", child)]
            )
    else:
        path = root / f"rollout-test-{identity}.jsonl"
        entries = [
            {"type": "session_meta", "timestamp": _TIMESTAMP, "payload": {"id": identity}},
            codex_item(_TIMESTAMP, {"type": "message", "role": "user", "content": [{"type": "input_text", "text": identity}]}),
        ]
        if child:
            entries.extend(
                [
                    codex_item(
                        _TIMESTAMP,
                        {"type": "function_call", "name": "mcp__agents_server__start", "call_id": "start", "arguments": "{}"},
                    ),
                    codex_item(
                        _TIMESTAMP,
                        {"type": "function_call_output", "call_id": "start", "output": json.dumps({"session_id": child})},
                    ),
                ]
            )
    write_jsonl(path, entries)
    return path


def _catalog(root: pathlib.Path, runtime: str, capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    option = "--catalog-claude-project" if runtime == "claude" else "--catalog-codex-history"
    argv = [option, str(root), "--since", "2026-10-05T00:00:00Z", "--observation-boundary", "2026-10-07T00:00:00Z"]
    assert evidence.main(argv) == 0
    return read_jsonl(capsys, raw=True)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_catalog_reuses_saved_record_values_and_follows_rewrites(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """2回目の照会は保存した記録ごとの値から同じ結果を返し、記録の書き換え後は新しい親子関係で返す。"""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    root = tmp_path / "records"
    for identity in (_PARENT, _CHILD):
        os.utime(_record(root, runtime, identity), (0, 0))
    first = _catalog(root, runtime, capsys)
    assert first[-1]["parent_record_count"] == 2
    assert _catalog(root, runtime, capsys) == first
    os.utime(_record(root, runtime, _PARENT, _CHILD), (1, 1))
    events = _catalog(root, runtime, capsys)
    parents = [(event["session_id"], event["descendant_count"]) for event in events if event["kind"] == "catalog-parent"]
    assert parents == [(_PARENT, 1)]
    assert events[-1]["parent_record_count"] == 1
