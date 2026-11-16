"""代理保存の所有者識別と、通常の登録・再開予約の非退行を確認する。"""

import contextlib
import json
import os
import pathlib

from agent_toolkit._common import registry_forwarding, session_registry
from agent_toolkit._common.process_identity import ProcessIdentity


def test_forwarded_mutations_do_not_write_local_registry(tmp_path: pathlib.Path) -> None:
    calls: list[tuple[str, dict]] = []

    def send(operation: str, arguments: dict) -> tuple[bool, object]:
        calls.append((operation, dict(arguments)))
        return True, operation in {"claim_resume", "release"}

    with registry_forwarding.bind(send):
        session_registry.publish("child", state=session_registry.PublicationState.PENDING_RUNNING, state_root=tmp_path)
        assert session_registry.claim_resume("child", 1, state_root=tmp_path)
        session_registry.release_resume_claim("child", 1, state_root=tmp_path)
        assert session_registry.release("child", reason="stopped", state_root=tmp_path)
        session_registry.record_interrupted_turn(
            "child", turn_seq=1, reason=session_registry.OWNER_SERVER_STOPPED, state_root=tmp_path
        )
    assert [operation for operation, _ in calls] == [
        "publish",
        "claim_resume",
        "release_resume_claim",
        "release",
        "record_interrupted_turn",
    ]
    assert calls[0][1]["state"] is session_registry.PublicationState.PENDING_RUNNING
    assert not (session_registry.registry_directory(tmp_path) / "child.json").exists()


def test_current_writer_preserves_original_controller_and_receiver(tmp_path: pathlib.Path, monkeypatch) -> None:
    original = session_registry.owner_process()
    controller = ProcessIdentity(os.getpid() + 100000, 123.0, "original-process")
    selected = registry_forwarding.PublicationIdentity(controller, "original-instance", controller)
    records = []

    def save(_path, body, **_kwargs):
        records.append(json.loads(body))

    monkeypatch.setattr(session_registry, "registry_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(session_registry, "_read_payload", lambda _path: {})
    monkeypatch.setattr(session_registry, "atomic_write", save)
    with registry_forwarding.bind(None, owner=selected):
        assert session_registry.owner_process() == controller
        session_registry.publish(
            "child",
            state=session_registry.PublicationState.PENDING_RUNNING,
            processes=(controller,),
            state_root=tmp_path,
        )
    assert len(records) == 1
    record = records[0]
    assert record["processes"][0]["pid"] == controller.pid
    assert record["effort_receiver"]["process"]["pid"] == controller.pid
    assert record["server_instance"] == "original-instance"
    assert session_registry.owner_process() == original


def test_late_proxy_cannot_overwrite_or_release_transferred_owner(monkeypatch, tmp_path):
    first = ProcessIdentity(1, 1.0, "first")
    second = ProcessIdentity(2, 2.0, "second")
    record = {
        "version": 2,
        "session_id": "child",
        "terminal": True,
        "status": "completed",
        "turn_seq": 3,
        "server_instance": "second",
        "processes": [session_registry.process_payload(second)],
    }
    locked = False
    writes = []

    @contextlib.contextmanager
    def lock(*_args, **_kwargs):
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    def read(_path):
        assert locked
        return record

    monkeypatch.setattr(session_registry, "registry_directory", lambda _root=None: tmp_path)
    monkeypatch.setattr(session_registry, "registry_lock", lock)
    monkeypatch.setattr(session_registry, "_read_payload", read)
    monkeypatch.setattr(session_registry, "atomic_write", lambda *args, **_kwargs: writes.append(args))
    with registry_forwarding.bind(None, owner=registry_forwarding.PublicationIdentity(first, "first", first)):
        session_registry.publish(
            "child",
            state=session_registry.PublicationState.RESULT_COMPLETED,
            turn_seq=3,
            processes=(first,),
            state_root=tmp_path,
        )
        assert not session_registry.release("child", reason="stopped", state_root=tmp_path)
        session_registry.record_interrupted_turn(
            "child",
            turn_seq=3,
            reason=session_registry.OWNER_SERVER_STOPPED,
            state_root=tmp_path,
        )
    assert not writes
