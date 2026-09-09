"""Codexコンパクションの計測記録を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._agents_server import codex, state
from agent_toolkit._agents_server import compaction_metrics as subject
from agent_toolkit._atk import config as atk_config


def _record_path(tmp_path: pathlib.Path, thread_id: str) -> pathlib.Path:
    return tmp_path / "agents-server" / "compaction" / f"{thread_id}.jsonl"


def test_append_records_duration_once_per_item(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """開始・完了時刻を秒へ換算し、同じitemの再通知は重複記録しない。"""
    monkeypatch.setattr(atk_config, "state_dir", lambda: tmp_path)

    subject.append_compaction_record("thread-1", "item-1", 1000, 2234)
    subject.append_compaction_record("thread-1", "item-1", 1000, 9000)

    records = [json.loads(line) for line in _record_path(tmp_path, "thread-1").read_text(encoding="utf-8").splitlines()]
    assert records == [
        {
            "version": 1,
            "thread_id": "thread-1",
            "item_id": "item-1",
            "started_at_ms": 1000,
            "completed_at_ms": 2234,
            "duration_seconds": 1.2,
        }
    ]


@pytest.mark.asyncio
async def test_notifications_record_only_completed_compaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """開始と完了の両通知が有効な場合だけ記録し、片側だけの保持はturn終端で破棄する。"""
    monkeypatch.setattr(atk_config, "state_dir", lambda: tmp_path)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager = codex.AppServerManager({session.session_id: session})

    await manager._handle_notification(  # pylint: disable=protected-access
        {
            "method": "item/started",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "startedAtMs": 1000,
                "item": {"id": "complete", "type": "contextCompaction"},
            },
        }
    )
    await manager._handle_notification(  # pylint: disable=protected-access
        {
            "method": "item/completed",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "completedAtMs": 3500,
                "item": {"id": "complete", "type": "contextCompaction"},
            },
        }
    )
    await manager._handle_notification(  # pylint: disable=protected-access
        {
            "method": "item/started",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "startedAtMs": 4000,
                "item": {"id": "partial", "type": "contextCompaction"},
            },
        }
    )

    records = [json.loads(line) for line in _record_path(tmp_path, session.session_id).read_text(encoding="utf-8").splitlines()]
    assert [record["item_id"] for record in records] == ["complete"]
    assert records[0]["duration_seconds"] == 2.5
    assert session.compaction_started_at_ms == {"partial": 4000}

    await manager._handle_notification(  # pylint: disable=protected-access
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": session.turn_id, "status": "completed", "error": None},
            },
        }
    )

    assert not session.compaction_started_at_ms
    assert [
        json.loads(line)["item_id"]
        for line in _record_path(tmp_path, session.session_id).read_text(encoding="utf-8").splitlines()
    ] == ["complete"]
