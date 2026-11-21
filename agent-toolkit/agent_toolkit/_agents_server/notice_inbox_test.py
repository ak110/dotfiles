"""`_agents_server/notice_inbox.py`の振る舞いを検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._agents_server import (
    notice_inbox,
    shared_layout,
)
from agent_toolkit._common import session_registry


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
    session_registry.publish(
        "target", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="parent", state_root=tmp_path
    )

    notices = notice_inbox.take_notices(
        "root", "target", tmp_path, recipient_session_id="parent", owner_status_file="parent.json"
    )

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


def test_wait_round_without_notices_reads_no_status_files(tmp_path: pathlib.Path, monkeypatch) -> None:
    """通知の無い巡では状態ファイルを読まず、通知が届いた巡では所有者を確かめて回収する。"""
    status = tmp_path / "agents-server" / "root"
    status.mkdir(parents=True)
    for writer in range(20):
        (status / f"writer-{writer}.json").write_text(json.dumps({"version": 1, "sessions": []}), encoding="utf-8")
    session_registry.publish(
        "target", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="parent", state_root=tmp_path
    )
    loaded: list[str] = []
    load_root = notice_inbox.status_reader.load_root

    def counting_load_root(root_session_id, *args, **kwargs):
        loaded.append(root_session_id)
        return load_root(root_session_id, *args, **kwargs)

    monkeypatch.setattr(notice_inbox.status_reader, "load_root", counting_load_root)

    def take() -> list[dict[str, str]]:
        return notice_inbox.take_notices(
            "root", "target", tmp_path, recipient_session_id="parent", owner_status_file="parent.json"
        )

    assert [take() for _round in range(3)] == [[], [], []]
    assert not loaded

    directory = shared_layout.notices_directory("root", tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    notice = {"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "届いた"}
    (directory / "target.20260907T010000000000.0123456789abcdef.json").write_text(json.dumps(notice), encoding="utf-8")
    assert take() == [{"body": "届いた"}]
    assert loaded == ["root"]


def test_reading_notices_is_repeatable_and_only_the_winner_delivers(tmp_path: pathlib.Path) -> None:
    """通知の読取や破損の診断は原本を消さず、CLIとMCPのうち実際に回収できた側だけが返す。"""
    directory = shared_layout.notices_directory("root", tmp_path)
    directory.mkdir(parents=True)
    session_registry.publish(
        "target", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="parent", state_root=tmp_path
    )
    damaged = directory / "target.bad.json"
    damaged.write_bytes(b"{")
    pending = notice_inbox.read_notices(
        "root", "target", tmp_path, recipient_session_id="parent", owner_status_file="root.json"
    )
    assert pending == notice_inbox.read_notices(
        "root", "target", tmp_path, recipient_session_id="parent", owner_status_file="root.json"
    )
    assert damaged.read_bytes() == b"{"
    taken = notice_inbox.take_notices(
        "root",
        "target",
        tmp_path,
        stash_directory=tmp_path / "run",
        recipient_session_id="parent",
        owner_status_file="root.json",
    )
    assert len(taken) == 1 and "破損" in taken[0]["body"]
    assert "退避しました" in taken[0]["body"] and "削除しました" not in taken[0]["body"]
    assert not notice_inbox.consume_notices(pending)
    assert (
        notice_inbox.take_notices(
            "root",
            "target",
            tmp_path,
            stash_directory=tmp_path / "run",
            recipient_session_id="parent",
            owner_status_file="root.json",
        )
        == taken
    )


@pytest.mark.parametrize("operation", ["read_text", "unlink"])
def test_notice_access_error_preserves_original_and_reports_retry(
    operation: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    directory = shared_layout.notices_directory("root", tmp_path)
    directory.mkdir(parents=True)
    session_registry.publish(
        "target", state=session_registry.PublicationState.PENDING_RUNNING, launcher_session_id="parent", state_root=tmp_path
    )
    original = directory / "target.pending.json"
    original.write_text(
        json.dumps({"version": 1, "session_id": "target", "sent_at": "2026-09-07T01:00:00Z", "body": "本文"}),
        encoding="utf-8",
    )
    method = getattr(pathlib.Path, operation)

    def denied(path: pathlib.Path, *args, **kwargs):
        if path == original:
            raise PermissionError("通知だけアクセス不可")
        return method(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, operation, denied)
    assert not notice_inbox.take_notices(
        "root", "target", tmp_path, recipient_session_id="parent", owner_status_file="root.json"
    )
    assert original.is_file()
    assert "原本を保持した" in caplog.text and "atk agents waitで再回収" in caplog.text
    assert str(original) in caplog.text
