"""公開した世代が終了しても、生存する世代が既存の会話の保存と受付を続ける。"""

# 世代間の受付と静止の内部状態を直接組み立てて検証する。
# pylint: disable=protected-access

import asyncio
import os
import pathlib
import subprocess
import sys
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import service_io, service_transport
from agent_toolkit._agents_server.service_context import ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_projection import ProjectionHub
from agent_toolkit._agents_server.service_registry import RegistryForwarder
from agent_toolkit._agents_server.service_resources import ResourceBroker

# 終了した受付先への接続の失敗はOSごとに例外が異なる（Windowsの名前付きパイプは`FileNotFoundError`）ため、Windowsでも確かめる。
pytestmark = pytest.mark.windows


def _exited_process() -> str:
    """起動単位を記録した後で終了したプロセスの識別を返す。"""
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as process:
        identity = service_transport.process_identity(process.pid)
    # Windowsは終了したプロセスもhandleが残る間は開けるため、起動した側のhandleを手放してから確かめる。
    del process
    assert not service_transport.process_alive(identity)
    return identity


def _target(directory: pathlib.Path, generation: str, epoch: int, process: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "epoch": epoch,
        "process": process,
        "endpoint": service_transport.endpoint(directory, generation),
        "server_instance": f"instance-{generation}",
    }


def _superseded(tmp_path: pathlib.Path, *, publish_new: bool = True) -> tuple[pathlib.Path, dict[str, Any], dict[str, Any]]:
    """このプロセスを生存する旧世代とし、終了した新世代を受付先として公開した管理領域を作る。"""
    directory = tmp_path / "agent-toolkit" / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    old = _target(directory, "old", 1, service_transport.process_identity(os.getpid()))
    new = _target(directory, "new", 2, _exited_process())
    for target, version in ((old, "1.0.0"), (new, "1.0.1")):
        registry.reserve(target["generation"], tmp_path / target["generation"], version)
        if target is old or publish_new:
            registry.publish(
                target["generation"],
                endpoint=target["endpoint"],
                process=target["process"],
                server_instance=target["server_instance"],
            )
    return directory, old, new


def _coordinator(directory: pathlib.Path, target: dict[str, Any]) -> ServiceCoordinator:
    coordinator = ServiceCoordinator(
        target, directory, ServiceManagers(), Mock(freeze=AsyncMock(), thaw=AsyncMock()), Mock(freeze=AsyncMock())
    )
    coordinator.bind_runtime(service_io.Runtime(target["generation"], target, ResourceBroker(target["generation"])))
    return coordinator


def test_registry_mutation_is_saved_by_surviving_generation_after_published_one_exited(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧世代の会話の登録更新を、終了した受付先へ転送して失敗させず、生存する旧世代が保存する。

    2026-11-18、Windowsの更新後処理が公開した新世代が起動確認の終了とともに終了し、
    旧世代の会話の状態保存が終了した受付先への接続で`FileNotFoundError`になった。
    """
    directory, _old, _new = _superseded(tmp_path)
    forwarder = RegistryForwarder("old", directory)
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(forwarder, "_save", saved.append)
    payload = {"operation": "publish"}
    forwarder._apply_sync(payload)
    assert saved == [payload]
    current = GenerationRegistry(directory).current()
    assert current is not None and current["generation"] == "old"


@pytest.mark.asyncio
async def test_projection_is_saved_by_surviving_generation_after_published_one_exited(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧世代の会話の状態表示と結果の保存も、終了した受付先へ転送して失敗させない。"""
    directory, _old, _new = _superseded(tmp_path)
    hub = ProjectionHub("old", directory)
    local = AsyncMock(return_value="saved")
    monkeypatch.setattr(hub, "_apply_local", local)
    payload = {"operation": "snapshot"}
    assert await hub.apply(payload) == "saved"
    local.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_surviving_generation_resumes_acceptance_after_published_one_exited(tmp_path: pathlib.Path) -> None:
    """公開が確定した後で新世代が終了した場合、旧世代は周期処理で受付を戻し、新しいturnを受け付けて退出しない。"""
    directory, old, new = _superseded(tmp_path)
    coordinator = _coordinator(directory, old)
    await coordinator.gate.observe_current(new)
    assert await coordinator.tick()
    assert coordinator.gate.current["generation"] == "old"
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
    assert not coordinator.can_retire()


@pytest.mark.asyncio
async def test_surviving_generation_releases_update_abandoned_by_exited_publisher(tmp_path: pathlib.Path) -> None:
    """新世代が公開の途中（旧世代の静止後、確定の通知前）で終了しても、旧世代は静止を解除して保存と新しいturnを再開する。"""
    directory, old, new = _superseded(tmp_path, publish_new=False)
    coordinator = _coordinator(directory, old)
    await coordinator.prepare("token", new)
    assert await coordinator.tick()
    assert not coordinator._intents
    registrations: Any = coordinator.registrations
    projections: Any = coordinator.projections
    registrations.thaw.assert_called_once_with("token")
    projections.thaw.assert_awaited_once_with("token")
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
