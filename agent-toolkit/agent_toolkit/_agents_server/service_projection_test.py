"""保存の待機中に始まった登録公開を待ってから、世代間の状態を写す。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import (
    retained_results,
    service_projection,
    service_state,
    service_transport,
    shared_roots,
    state,
)
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import session_layout


class _Codec:
    def encode(self, value: Any) -> Any:
        return value


class _Hub:
    def __init__(self, state_root: pathlib.Path, codec: Any = None) -> None:
        self.state_root = state_root
        self._codec = codec or _Codec()
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def request(self, _identity: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((operation, payload))
        return {"published": [], "delivered": []}


@pytest.mark.asyncio
async def test_flush_waits_registration_started_while_draining_other_session(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = state.SessionState("first", str(tmp_path), engine="codex")
    second = state.SessionState("second", str(tmp_path), engine="codex")
    hub = _Hub(tmp_path)
    writer = service_projection.ServiceStatusWriter(
        {"first": first, "second": second},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        cast(service_projection.ProjectionHub, hub),
    )
    writer._active = True  # pylint: disable=protected-access
    published: list[str] = []

    async def publish_first() -> None:
        await asyncio.sleep(0)
        published.append("first")

    async def publish_second(**_kwargs: Any) -> None:

        first._registration_task = asyncio.create_task(publish_first())  # pylint: disable=protected-access

    monkeypatch.setattr(second, "publish_registration_async", publish_second)
    await writer.flush_async()
    assert published == ["first"]
    assert not first.registration_pending
    assert [operation for operation, _payload in hub.requests] == ["snapshot"]
    assert [service_state.restore(item).session_id for item in hub.requests[0][1]["sessions"]] == ["first", "second"]


def _writer(hub: _Hub, sessions: dict[str, state.SessionState], **kwargs: Any) -> service_projection.ServiceStatusWriter:
    writer = service_projection.ServiceStatusWriter(
        sessions,
        shared_roots.StatusFileIdentity("root", "root.json", None),
        cast(service_projection.ProjectionHub, hub),
        **kwargs,
    )
    writer._active = True  # pylint: disable=protected-access
    return writer


@pytest.mark.asyncio
async def test_retain_result_waits_registration_started_as_previous_one_finished(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = state.SessionState("first", str(tmp_path), engine="codex")
    hub = _Hub(tmp_path)
    writer = _writer(hub, {"first": session})
    published: list[str] = []

    async def publish(name: str) -> None:
        await asyncio.sleep(0)
        published.append(name)

    def start_next() -> None:
        session._registration_task = asyncio.create_task(publish("next"))  # pylint: disable=protected-access

    async def previous() -> None:
        await publish("previous")

        asyncio.get_running_loop().call_soon(start_next)

    session._registration_task = asyncio.create_task(previous())  # pylint: disable=protected-access
    pending_at_request: list[bool] = []
    forward = hub.request

    async def request(identity: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        pending_at_request.append(session.registration_pending)
        return await forward(identity, operation, payload)

    monkeypatch.setattr(hub, "request", request)
    await writer.retain_result_async(session)
    assert [operation for operation, _payload in hub.requests] == ["result_state_async"]
    assert pending_at_request == [False]


@pytest.mark.asyncio
async def test_startup_publishes_registry_derived_resume_state_kept_as_expired(tmp_path: pathlib.Path) -> None:
    """登録簿から復元した再開状態を期限切れに持つ会話でも、起動時の状態公開が型を保って進む。

    2026-11-18、復旧に失敗した子のこの状態を符号化できず、管理サービスの起動が毎回失敗してlist・show・send_messageが使えなかった。
    """
    resume = state.RegistryResumeState(
        "child",
        str(tmp_path / "removed"),
        None,
        None,
        "claude",
        turn_seq=3,
        status="interrupted",
        result_root_session_id="root",
    )
    codec = service_state._StateCodec()  # pylint: disable=protected-access
    hub = _Hub(tmp_path, codec)
    manager = AgentsServerManager(_writer(hub, {}, expired_sessions={"child": resume}))
    await manager.initialize_delegation_recovery()
    assert [operation for operation, _payload in hub.requests] == ["snapshot"]
    expired = codec.decode(json.loads(json.dumps(hub.requests[0][1]["expired"])))
    assert expired == {"child": resume}
    assert type(expired["child"]) is state.RegistryResumeState  # pylint: disable=unidiomatic-typecheck


_PARENTS = ("first-parent", "second-parent")


def _identity(parent: str) -> dict[str, Any]:
    return dataclasses.asdict(shared_roots.StatusFileIdentity(parent, "root.json", None))


async def _observed(hub: service_projection.ProjectionHub, parent: str, progress: str, effort: str | None) -> dict[str, Any]:
    session = state.SessionState(f"{parent}-child", "/work", engine="codex", effort=effort, announced=True)
    session.set_progress(progress)
    return {
        "sessions": [await service_state.snapshot(session)],
        "expired": hub._codec.encode({}),  # pylint: disable=protected-access
        "published": [],
    }


async def _published_hub(tmp_path: pathlib.Path, groups: dict[str, tuple[str, str | None]]) -> service_projection.ProjectionHub:
    directory = tmp_path / "agent-toolkit" / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    process = service_transport.process_identity(os.getpid())
    for generation, version in (("old", "1.0.0"), ("new", "1.0.1")):
        registry.reserve(generation, tmp_path / generation, version)
    registry.publish("old", endpoint="old", process=process)
    hub = service_projection.ProjectionHub("new", directory)
    inventory = {
        "writers": [
            {
                "identity": _identity(parent),
                "groups": {source: await _observed(hub, parent, *value) for source, value in groups.items()},
                "published": [],
            }
            for parent in _PARENTS
        ]
    }
    await hub.seed(inventory)
    registry.publish("new", endpoint="new", process=process)
    return hub


def _displayed(tmp_path: pathlib.Path, parent: str) -> tuple[str, str | None]:
    path = session_layout.status_directory(parent, tmp_path / "agent-toolkit") / "root.json"
    [session] = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    return session["progress"], session["effort"]


async def _hold_first_parent(
    hub: service_projection.ProjectionHub, monkeypatch: pytest.MonkeyPatch, release: asyncio.Event
) -> tuple[asyncio.Task, asyncio.Lock]:
    """1件目の親会話で通知の読み取りに書込主体のロックを持たせ、後続の保存と再描画をロックの順に並ばせる。"""
    key = (_PARENTS[0], "root.json")

    async def take_notices_async(session_id: str) -> list:
        del session_id
        await release.wait()
        return []

    monkeypatch.setattr(hub.writers[key], "take_notices_async", take_notices_async)
    reading = asyncio.create_task(
        hub.apply(
            {
                "identity": _identity(_PARENTS[0]),
                "source": "old",
                "operation": "take_notices_async",
                "arguments": {"session_id": f"{_PARENTS[0]}-child"},
            }
        )
    )
    lock = hub._locks[key]  # pylint: disable=protected-access
    await _queued(lock, 0)
    return reading, lock


async def _queued(lock: asyncio.Lock, waiters: int) -> None:
    """ロックが保持され、指定数の要求が待つまで進める。保存と再描画の順序を待ち時間に頼らずに固定する。"""
    while not lock.locked() or len(lock._waiters or ()) < waiters:  # pylint: disable=protected-access
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_publication_keeps_progress_saved_by_previous_generation_while_activating(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開が状態表示を再適用するまでに旧世代が保存した進捗とeffortを、公開の後も表示に残す。

    2026-11-20の独立検証で、公開がロックを取る前に選んだ観測を再適用し、保存済みの進捗とeffortを空へ戻した。
    """
    hub = await _published_hub(tmp_path, {"old": ("", None)})
    release = asyncio.Event()
    async with asyncio.timeout(10):
        reading, lock = await _hold_first_parent(hub, monkeypatch, release)
        saved = await _observed(hub, _PARENTS[0], "probe-checkpoint-2", "high")
        saving = asyncio.create_task(
            hub.apply({"identity": _identity(_PARENTS[0]), "source": "old", "operation": "snapshot", "arguments": saved})
        )
        await _queued(lock, 1)
        activating = asyncio.create_task(hub.activate())
        await _queued(lock, 2)
        release.set()
        await asyncio.gather(reading, saving, activating)
    assert _displayed(tmp_path, _PARENTS[0]) == ("probe-checkpoint-2", "high")
    assert _displayed(tmp_path, _PARENTS[1]) == ("", None)


@pytest.mark.asyncio
async def test_progress_saved_while_other_generation_leaves_parent_stays_displayed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = await _published_hub(tmp_path, {"old": ("old-progress", "low"), "new": ("", None)})
    release = asyncio.Event()
    async with asyncio.timeout(10):
        reading, lock = await _hold_first_parent(hub, monkeypatch, release)
        leaving = asyncio.create_task(
            hub.apply({"identity": _identity(_PARENTS[0]), "source": "old", "operation": "deactivate", "arguments": {}})
        )
        await _queued(lock, 1)
        saved = await _observed(hub, _PARENTS[0], "probe-checkpoint-2", "high")
        saving = asyncio.create_task(
            hub.apply({"identity": _identity(_PARENTS[0]), "source": "new", "operation": "snapshot", "arguments": saved})
        )
        await _queued(lock, 2)
        release.set()
        await asyncio.gather(reading, leaving, saving)
    assert _displayed(tmp_path, _PARENTS[0]) == ("probe-checkpoint-2", "high")


@pytest.mark.asyncio
async def test_frozen_projection_preserves_exact_results_across_activation(tmp_path: pathlib.Path) -> None:
    directory = tmp_path / "services" / "agents-server"
    hub = service_projection.ProjectionHub("source", directory)
    successor = service_projection.ProjectionHub("successor", directory)
    session = state.SessionState("child", str(tmp_path), announced=True, turn_seq=1)
    identity = shared_roots.StatusFileIdentity("parent", "root.json", None)
    sender = service_projection.ServiceStatusWriter({"child": session}, identity, hub)
    try:
        await sender.activate_async()
        for seq in (1, 2):
            session.turn_seq = seq
            session.status = "completed"
            session.agent_message = f"結果{seq}"
            session.record_turn_completed()
            session.touch()
            await sender.flush_async()
        await hub.freeze("switch")
        inventory = hub.inventory()
        for seq in (1, 2):
            result, error = retained_results.take_result("parent", "child", "root.json", collector="cli", state_root=tmp_path)
            assert error is None and result is not None and result["agent_message"] == f"結果{seq}"
        await successor.seed(inventory)
        await successor.activate()
        writer = successor.writers["parent", "root.json"]
        assert writer.sessions["child"].result_delivered
        assert not writer.result_exists("child")
    finally:
        await hub.thaw("switch")
        await hub.close()
        await successor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frozen_turn", "in_slot", "saved_again", "available"),
    [(1, None, False, False), (2, None, False, False), (2, 1, False, True), (2, 2, False, False), (2, 1, True, False)],
)
async def test_legacy_inventory_after_cli_claim(
    tmp_path: pathlib.Path, frozen_turn: int, in_slot: int | None, saved_again: bool, available: bool
) -> None:
    successor = service_projection.ProjectionHub("next", tmp_path / "services" / "agents-server")
    child = state.SessionState("child", str(tmp_path), announced=True, turn_seq=frozen_turn, status="completed")
    child.agent_message = f"結果{frozen_turn}"
    child.record_turn_completed()
    child.touch()
    group = {
        "sessions": [await service_state.snapshot(child)],
        "expired": {"kind": "dict", "items": {}},
        "published": [] if frozen_turn == 1 else ["child"],
    }
    directory = session_layout.results_directory("parent", tmp_path)
    observed = {}
    if in_slot is not None:
        directory.mkdir(parents=True)
        original = directory / "child.json"
        original.write_text(json.dumps({"turn_seq": in_slot, "finalized_at": child.finalized_at if in_slot == 2 else "first"}))
        legacy_status = {"writer_process": {"pid": 123}, "sessions": [{"session_id": "child", "status": "completed"}]}
        (directory.parent / "root.json").write_text(json.dumps(legacy_status))
        observed = service_projection.observe_legacy_slots(tmp_path, "123:start")
        original.unlink()
        if saved_again:
            (directory.parent / "root.json").write_text(json.dumps({**legacy_status, "heartbeat_at": child.finalized_at}))
    try:
        inventory = {"writers": [{"identity": _identity("parent"), "groups": {"old": group}, "published": ["child"]}]}
        await successor.seed(inventory, observed=observed)
        assert not (directory / ".published").exists()
        await successor.activate()
        writer = successor.writers["parent", "root.json"]
        assert writer.sessions["child"].result_delivered is not available
        result, error = writer.take_result("child", collector="cli")
        assert error is None
        assert (result["agent_message"] if result else None) == (child.agent_message if available else None)
        await successor.activate()
        assert writer.take_result("child", collector="cli") == (None, None)
    finally:
        await successor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("already_saved", "reported_published"), [(True, False), (False, False), (False, True)])
async def test_legacy_snapshot_receipt_cannot_consume_next_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, already_saved: bool, reported_published: bool
) -> None:
    """中間版が空の旧名を見て最新turnを受領済みと返しても、その失敗診断を1回回収できる。"""
    child = state.SessionState("child", str(tmp_path), announced=True, turn_seq=2, status="failed")
    child.error = {"message": "失敗の理由"}
    child.record_turn_completed()
    child.touch()
    hub = _Hub(tmp_path, service_state._StateCodec())  # pylint: disable=protected-access
    writer = _writer(hub, {"child": child})
    if already_saved:
        writer.retain_result(child)

    async def legacy_snapshot(_identity: Any, operation: str, _payload: dict) -> Any:
        if operation == "result_state_async":
            return "unpublished"
        assert operation == "snapshot"
        if reported_published:
            return {"published": ["child"], "delivered": []}
        return {"published": [], "delivered": [["child", 2, child.finalized_at]]}

    monkeypatch.setattr(hub, "request", legacy_snapshot)
    await writer.flush_async()
    assert not child.result_delivered
    result, error = writer.take_result("child", collector="cli")
    assert error is None and result is not None and result["error"] == child.error
    await writer.flush_async()
    assert child.result_delivered
    assert writer.take_result("child", collector="cli") == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["during_request", "after_response", "lost_response"])
async def test_sender_owns_result_through_legacy_snapshot(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, claim: str
) -> None:
    """旧hubが保存要求を受け取る前に確定した原本を、応答喪失や再試行をまたいでも1回だけ回収する。"""
    child = state.SessionState("child", str(tmp_path), announced=True, turn_seq=2, status="failed")
    child.error = {"message": "turn 2"}
    child.record_turn_completed()
    child.touch()
    hub = _Hub(tmp_path, service_state._StateCodec())  # pylint: disable=protected-access
    writer = _writer(hub, {"child": child})
    slot = session_layout.results_directory("root", tmp_path) / "child.json"
    received = []

    def collect() -> None:
        received.append(json.loads(slot.read_text())["error"])
        slot.unlink()

    async def legacy(_identity: Any, operation: str, payload: dict) -> Any:
        if operation == "result_state_async":
            return "unpublished"
        [item] = payload["sessions"]
        view = service_state.restore(item)
        assert view.status == "failed"
        assert not view.result_available and not view.result_delivered
        if not received and claim != "after_response":
            collect()
            if claim == "lost_response":
                raise ConnectionResetError
        return {"delivered": [], "published": ["child"]}

    monkeypatch.setattr(hub, "request", legacy)
    if claim == "lost_response":
        with pytest.raises(ConnectionResetError):
            await writer.flush_async()
    else:
        await writer.flush_async()
    if claim == "after_response":
        collect()
    await writer.flush_async()
    assert received == [child.error]
    assert child.result_delivered
    assert writer.take_result("child", collector="cli") == (None, None)


@pytest.mark.asyncio
async def test_unadmitted_sender_does_not_publish_its_initial_result(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = state.SessionState("child", str(tmp_path), announced=True, turn_seq=1, status="completed")
    child.record_turn_completed()
    child.touch()
    hub = _Hub(tmp_path, service_state._StateCodec())  # pylint: disable=protected-access
    writer = _writer(hub, {"child": child})

    async def reject(_identity: Any, _operation: str, _payload: dict) -> Any:
        raise RuntimeError("別のwriterが所有中")

    monkeypatch.setattr(hub, "request", reject)
    with pytest.raises(RuntimeError, match="別のwriter"):
        await writer.activate_async()
    assert not session_layout.results_directory("root", tmp_path).exists()
