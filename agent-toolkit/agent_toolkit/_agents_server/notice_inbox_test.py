"""`_agents_server/notice_inbox.py`の振る舞いを検証する。"""

from __future__ import annotations

import json
import pathlib

from agent_toolkit._agents_server import (
    notice_inbox,
    shared_layout,
)


def test_take_notices_keeps_invalid_values_and_removes_ordered_valid_notices(tmp_path: pathlib.Path) -> None:
    """不正通知を保持し、正常通知だけを送信時刻とファイル名の順で回収する。"""
    directory = shared_layout.notices_directory("root", tmp_path)
    directory.mkdir(parents=True)
    payloads = {
        "invalid-version.json": {"version": 2, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "本文"},
        "invalid-session.json": {"version": 1, "session_id": "other", "sent_at": "2026-09-07T01:00:00Z", "body": "本文"},
        "invalid-sent-at.json": {"version": 1, "session_id": "target", "sent_at": 1, "body": "本文"},
        "invalid-body.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": ["本文"]},
        "valid-late.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T02:00:00Z", "body": "後"},
        "valid-same-b.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "同時刻B"},
        "valid-same-a.json": {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "同時刻A"},
    }
    for name, payload in payloads.items():
        (directory / name).write_text(json.dumps(payload), encoding="utf-8")

    notices = notice_inbox.take_notices("root", "target", tmp_path)

    assert notices == [
        {"body": "同時刻A"},
        {"body": "同時刻B"},
        {"body": "後"},
    ]
    assert {path.name for path in directory.iterdir()} == {
        "invalid-version.json",
        "invalid-session.json",
        "invalid-sent-at.json",
        "invalid-body.json",
    }
