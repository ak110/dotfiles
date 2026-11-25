import itertools
import pathlib
from unittest.mock import Mock

from agent_toolkit._agents_server import service_io
from agent_toolkit._agents_server.service_context import ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_resources import ResourceBroker


def test_shutdown_guard_keeps_ownership_for_all_orderings_of_three_obligations(tmp_path: pathlib.Path) -> None:
    target = {"generation": "G1", "epoch": 1}
    coordinator = ServiceCoordinator(target, tmp_path, ServiceManagers(), Mock(), Mock())
    coordinator.bind_runtime(service_io.Runtime("G1", target, ResourceBroker("G1")))
    moving = Mock()
    obligations = {
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
