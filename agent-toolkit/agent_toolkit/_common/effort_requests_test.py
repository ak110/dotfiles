"""移譲中の遅着要求を同一ファイル・同一期限で回収する境界。"""

import json
import time

import pytest

from agent_toolkit._common import effort_requests, session_registry


@pytest.mark.parametrize("condition", ["late", "expired", "answered", "unsupported", "deleted"])
def test_pending_preserves_request_identity_during_transfer(tmp_path, monkeypatch, condition):
    owner = session_registry.owner_process()
    assert owner is not None
    directory = session_registry.registry_directory(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    previous = owner.payload()
    previous["pid"] += 1
    previous["create_time"] += 1
    packet = {
        "version": 1,
        "session_id": "child",
        "actor": "parent",
        "direction": "up",
        "deadline": time.monotonic() + (-1 if condition == "expired" else 30),
        "owner": previous,
    }
    path = directory / "same-id.effort-request"
    path.write_text(json.dumps(packet), encoding="utf-8")
    record = {"processes": [owner.payload()], "effort_receiver": {"version": 1, "process": owner.payload()}}
    if condition == "unsupported":
        record.pop("effort_receiver")
    (directory / "child.json").write_text(json.dumps(record), encoding="utf-8")
    if condition == "answered":
        path.with_suffix(".effort-response").write_text("{}", encoding="utf-8")
    original_lock = session_registry.registry_lock
    if condition == "deleted":

        def lock(*args, **kwargs):
            path.unlink()
            return original_lock(*args, **kwargs)

        monkeypatch.setattr(session_registry, "registry_lock", lock)
    pending = effort_requests.pending(state_root=tmp_path)
    if condition == "late":
        expected = {**packet, "owner": owner.payload()}
        assert pending == [(path, expected)]
        assert json.loads(path.read_text(encoding="utf-8")) == expected
        assert effort_requests.pending(state_root=tmp_path) == pending
    else:
        assert not pending
        if condition == "deleted":
            assert not path.exists()
        else:
            assert json.loads(path.read_text(encoding="utf-8")) == packet
