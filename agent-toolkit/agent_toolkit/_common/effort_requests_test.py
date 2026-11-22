"""移譲中の遅着要求を同一ファイル・同一期限で回収する境界と、要求側の後始末と重なった応答を残さないこと。"""

import json
import time

import pytest

from agent_toolkit._common import atomic_file, effort_requests, registry_requests, session_registry


@pytest.mark.parametrize("condition", ["late", "expired", "answered", "legacy-answered", "unsupported", "deleted"])
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
    registry_requests.write_request(path, packet)
    stored = {**packet, "request_id": path.stem}
    record = {"processes": [owner.payload()], "effort_receiver": {"version": 1, "process": owner.payload()}}
    if condition == "unsupported":
        record.pop("effort_receiver")
    (directory / "child.json").write_text(json.dumps(record), encoding="utf-8")
    if condition == "answered":
        effort_requests.respond(path, {"session_id": "child", "effort": "medium"})
    elif condition == "legacy-answered":
        path.with_suffix(".effort-response").write_text("{}", encoding="utf-8")
    if condition == "deleted":
        original_read = type(path).read_text

        def read_text(target, *args, **kwargs):
            if target == path:
                path.unlink()
            return original_read(target, *args, **kwargs)

        monkeypatch.setattr(type(path), "read_text", read_text)
    pending = effort_requests.pending(state_root=tmp_path)
    if condition == "late":
        expected = {**stored, "owner": owner.payload()}
        assert pending == [(path, expected)]
        assert json.loads(path.read_text(encoding="utf-8")) == stored
        assert effort_requests.pending(state_root=tmp_path) == pending
    else:
        assert not pending
        if condition == "deleted":
            assert not path.exists()
        elif condition == "answered":
            assert not path.exists()
            completed = json.loads(path.with_suffix(".effort-request-result").read_text(encoding="utf-8"))
            assert completed == {**stored, "result": {"session_id": "child", "effort": "medium"}}
        else:
            assert json.loads(path.read_text(encoding="utf-8")) == stored


@pytest.mark.parametrize("withdrawn", [False, True])
def test_response_remains_only_while_request_is_held(tmp_path, monkeypatch, withdrawn):
    """旧senderが要求と応答を消す順で終了しても、競合後に応答だけを残さない。"""
    directory = session_registry.registry_directory(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    request = directory / "token.effort-request"
    request.write_text("{}", encoding="utf-8")
    response = request.with_suffix(".effort-response")
    original_write = atomic_file.atomic_write

    def write_after_requester_cleanup(path, content, **kwargs):
        if withdrawn:
            # 要求側（旧版を含む）は期限切れで待機を終え、要求、応答の順に消す。
            request.unlink()
            response.unlink(missing_ok=True)
        original_write(path, content, **kwargs)

    monkeypatch.setattr(atomic_file, "atomic_write", write_after_requester_cleanup)
    effort_requests.respond(request, {"session_id": "child", "effort": "medium"})
    if withdrawn:
        assert not request.exists() and not response.exists()
    else:
        assert json.loads(response.read_text(encoding="utf-8")) == {"session_id": "child", "effort": "medium"}
