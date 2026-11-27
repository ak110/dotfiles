import asyncio
import itertools
import json
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import service, service_io, service_rpc, service_transport
from agent_toolkit._agents_server.service_context import ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_resources import ResourceBroker


def test_shutdown_guard_keeps_ownership_for_all_orderings_of_three_obligations(tmp_path: pathlib.Path) -> None:
    target = {"generation": "G1", "epoch": 1, "process": service_transport.process_identity(os.getpid())}
    coordinator = ServiceCoordinator(target, tmp_path, ServiceManagers(), Mock(), Mock())
    coordinator.bind_runtime(service_io.Runtime("G1", target, ResourceBroker("G1")))
    moving = Mock()
    obligations: dict[str, dict[str, Any]] = {
        "conversation": coordinator.managers.conversations,
        "move": coordinator._moving,  # pylint: disable=protected-access
        "update": coordinator._updates,  # pylint: disable=protected-access
    }
    events = tuple((name, acquire) for name in obligations for acquire in (True, False))
    checked = 0
    for order in itertools.permutations(events):
        if any(order.index((name, False)) < order.index((name, True)) for name in obligations):
            continue
        checked += 1
        active: set[str] = set()
        assert coordinator.can_shutdown()
        for name, acquire in order:
            store = obligations[name]
            if acquire:
                active.add(name)
                store[name] = moving
            else:
                active.remove(name)
                del store[name]
            moving.done.return_value = "move" not in active
            assert coordinator.can_shutdown() == (not active), (order, active)
    assert checked == 90


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_first", [False, True])
@pytest.mark.parametrize("stop_before_transfer", [False, True])
async def test_service_shutdown_waits_for_transferred_resource(
    tmp_path: pathlib.Path, publish_first: bool, stop_before_transfer: bool
) -> None:
    directory = tmp_path / "state" / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    registry.reserve("G1", tmp_path, "1.0.0")
    shutdown = asyncio.Event()
    force_stop = asyncio.Event()
    broker = ResourceBroker("G1")
    closed = AsyncMock()
    resource = broker.register({"close": closed, "read": AsyncMock(return_value="retained")})
    serving = asyncio.create_task(service.run(directory, "G1", shutdown_requested=shutdown, stop=force_stop, resources=broker))
    try:
        async with asyncio.timeout(12):
            ready = directory / "G1.json"
            while not ready.exists():
                assert not serving.done()
                await asyncio.sleep(0.01)
            target = json.loads(ready.read_text(encoding="utf-8"))
            if publish_first:
                assert (await service_rpc.request(target, "publish", {}))["generation"] == "G1"
            if stop_before_transfer:
                shutdown.set()
            assert await service_rpc.request(
                target, "transfer", {"resource_id": resource, "expected_owner": "G1", "new_owner": "G2"}
            )
            if not stop_before_transfer:
                shutdown.set()
            if not publish_first:
                assert (await service_rpc.request(target, "publish", {}))["generation"] == "G1"
            await asyncio.sleep(0.6)
            assert not serving.done()
            assert (
                await service_rpc.request(
                    target, "call", {"resource_id": resource, "owner_generation": "G2", "operation": "read", "arguments": {}}
                )
                == "retained"
            )
            closed.assert_not_called()
            await service_rpc.request(target, "release", {"resource_id": resource, "owner_generation": "G2"})
            await serving
            closed.assert_awaited_once()
    finally:
        force_stop.set()
        await asyncio.gather(serving, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["abandoned", "deferred", "published"])
async def test_unpublished_candidate_expiry_respects_explicit_deferral(tmp_path: pathlib.Path, mode: str) -> None:
    directory = tmp_path / "state" / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    registry.reserve("G1", tmp_path, "1.0.0")
    force_stop = asyncio.Event()
    serving = asyncio.create_task(
        service.run(directory, "G1", stop=force_stop, unpublished_grace=None if mode == "deferred" else 0.75)
    )
    try:
        async with asyncio.timeout(10):
            if mode != "abandoned":
                ready = directory / "G1.json"
                while not ready.exists():
                    assert not serving.done()
                    await asyncio.sleep(0.01)
                target = json.loads(ready.read_text(encoding="utf-8"))
                if mode == "published":
                    assert (await service_rpc.request(target, "publish", {}))["generation"] == "G1"
                await asyncio.sleep(1.1)
                assert await service_rpc.request(target, "ping", {}) == {"generation": "G1"}
            else:
                await serving
            assert (registry.current() is not None) == (mode == "published")
    finally:
        force_stop.set()
        await asyncio.gather(serving, return_exceptions=True)
