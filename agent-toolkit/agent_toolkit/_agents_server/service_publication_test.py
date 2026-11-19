"""公開した世代が終了しても、生存する世代が既存の会話の保存と受付を続ける。"""

# 世代間の受付と静止の内部状態を直接組み立てて検証する。
# pylint: disable=protected-access

import asyncio
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import (
    service,
    service_coordinator,
    service_forward,
    service_io,
    service_rpc,
    service_transport,
    shared_roots,
)
from agent_toolkit._agents_server.service_context import ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_projection import ProjectionHub
from agent_toolkit._agents_server.service_registry import RegistryForwarder
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._common import runtime_environment

# 終了した受付先への接続の失敗はOSごとに例外が異なる（Windowsの名前付きパイプは`FileNotFoundError`）ため、Windowsでも確かめる。
pytestmark = pytest.mark.windows


def _exited_process() -> str:
    """起動単位を記録した後で終了したプロセスの識別を返す。"""
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as process:
        identity = service_transport.process_identity(process.pid)
    # Windowsは終了したプロセスもhandleが残る間は開けるため、起動した側のhandleを手放し、他のhandle保持者が手放すまで待つ。
    del process
    deadline = time.monotonic() + 10
    while service_transport.process_alive(identity) and time.monotonic() < deadline:
        time.sleep(0.05)
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
    projections = Mock(freeze=AsyncMock(), thaw=AsyncMock(), seed=AsyncMock(), activate=AsyncMock())
    coordinator = ServiceCoordinator(target, directory, ServiceManagers(), projections, Mock(freeze=AsyncMock()))
    coordinator.bind_runtime(service_io.Runtime(target["generation"], target, ResourceBroker(target["generation"])))
    return coordinator


@pytest.mark.asyncio
async def test_registry_mutation_is_saved_by_surviving_generation_after_published_one_exited(
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
    await forwarder.apply(payload)
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
    # 静止中は監視の周期処理も保存の待機で止まり得るため、周期処理を呼ばずに解除を待つ。
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
    registrations: Any = coordinator.registrations
    projections: Any = coordinator.projections
    registrations.thaw.assert_called_once_with("token")
    projections.thaw.assert_awaited_once_with("token")


@pytest.mark.parametrize(
    "failing_stage", ["projections.thaw", "registrations.thaw", "gate.thaw", "gate.observe_current", "registry.restore"]
)
@pytest.mark.asyncio
async def test_release_runs_every_stage_and_wakes_commit_even_if_one_stage_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failing_stage: str
) -> None:
    """静止の解除のどの段が失敗しても、残りの段は実行され、確定を待つ`commit_update`は速やかに戻る。"""
    directory, old, new = _superseded(tmp_path, publish_new=False)
    coordinator = _coordinator(directory, old)
    projections: Any = coordinator.projections
    registrations: Any = coordinator.registrations
    owners: dict[str, Any] = {
        "projections": projections,
        "registrations": registrations,
        "gate": coordinator.gate,
        "registry": coordinator.registry,
    }
    owner_name, attribute = failing_stage.split(".")
    failure = OSError(failing_stage)
    stage: Any = (AsyncMock if owner_name in ("gate", "projections") else Mock)(side_effect=failure)
    monkeypatch.setattr(owners[owner_name], attribute, stage)
    await coordinator.prepare("token", new)
    update = coordinator._updates["token"]
    async with asyncio.timeout(1.5):
        if failing_stage == "registry.restore":
            await update.released.wait()
        else:
            await coordinator.commit_update("token", old)
    if failing_stage != "projections.thaw":
        projections.thaw.assert_awaited_once_with("token")
    if failing_stage != "registrations.thaw":
        registrations.thaw.assert_called_once_with("token")
    assert "token" not in coordinator._updates
    if failing_stage != "gate.thaw":
        async with asyncio.timeout(1.5), coordinator.gate.admit(new_turn=True):
            pass


@pytest.mark.asyncio
async def test_surviving_generation_returns_acceptance_left_by_publisher_exited_before_commit(tmp_path: pathlib.Path) -> None:
    """公開元が受付を公開した後、確定の通知前に終了したら、旧世代は受付を自身へ戻して新しいturnを受け付ける。"""
    directory, old, new = _superseded(tmp_path)
    coordinator = _coordinator(directory, old)
    await coordinator.prepare("token", new)
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
    current = GenerationRegistry(directory).current()
    assert current is not None and current["generation"] == "old"


@pytest.mark.asyncio
async def test_late_commit_from_exited_publisher_keeps_acceptance_on_survivor(tmp_path: pathlib.Path) -> None:
    """公開元が確定を送った直後に終了し、旧世代が受付を戻した後に確定が届いても、終了した世代へ受付を向け直さない。"""
    directory, old, new = _superseded(tmp_path)
    coordinator = _coordinator(directory, old)
    await coordinator.prepare("token", new)
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
    await coordinator.commit_update("token", new)
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass


async def _hold_new_turn(coordinator: ServiceCoordinator, release: asyncio.Event) -> None:
    async with coordinator.gate.admit(new_turn=True):
        await release.wait()


@pytest.mark.parametrize("outcome", ["publisher_exited", "commit_after_prepare_gave_up"])
@pytest.mark.asyncio
async def test_update_settled_while_preparing_resumes_saving_after_preparation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """準備が受付済みの新turnの回収を待つ間に、公開元が終了するか確定を通知しても、準備の後で保存と新しいturnを再開する。

    準備は新turnを回収してから状態保存と登録簿の更新を静止する。それより先に静止を解くと、後から掛かる静止を解く主体が残らない。
    確定は、公開元の準備の要求が待機上限に達して失敗した場合にも届く。
    """
    directory, old, new = _superseded(tmp_path, publish_new=False)
    if outcome == "commit_after_prepare_gave_up":
        new = dict(new, process=service_transport.process_identity(os.getpid()))
    registrations = RegistryForwarder("old", directory)
    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(registrations, "_save", saved.append)
    coordinator = ServiceCoordinator(old, directory, ServiceManagers(), ProjectionHub("old", directory), registrations)
    coordinator.bind_runtime(service_io.Runtime("old", old, ResourceBroker("old")))
    release = asyncio.Event()
    holder = asyncio.create_task(_hold_new_turn(coordinator, release))
    await asyncio.sleep(0)
    preparing = asyncio.create_task(coordinator.prepare("token", new))
    committing = None
    if outcome == "commit_after_prepare_gave_up":
        await asyncio.sleep(0)
        committing = asyncio.create_task(coordinator.commit_update("token", old))
    # 公開元の終了の観測か確定の処理が、準備が静止を掛け終える前に進む時間を置く。
    await asyncio.sleep(0.3)
    release.set()
    async with asyncio.timeout(5):
        await holder
        await preparing
        if committing is not None:
            await committing
        await coordinator.projections.apply(
            {
                "identity": dataclasses.asdict(shared_roots.StatusFileIdentity("root", "root.json", None)),
                "source": "old",
                "operation": "seed",
                "arguments": {"groups": {}, "published": []},
            }
        )
        await registrations.apply({"operation": "publish"})
        async with coordinator.gate.admit(new_turn=True):
            pass
    assert saved == [{"operation": "publish"}]


@pytest.mark.asyncio
async def test_relaunched_generation_takes_over_from_survivor_after_acceptance_returned(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終了した世代を起動し直すと、生存する旧世代の保存を静止して引き継いでから受付先になる。

    起動し直した世代は受付を戻す前のepochで起動していても、戻した世代より新しい候補として公開できる。
    """
    directory, _old, new = _superseded(tmp_path)
    relaunched = dict(new, process=service_transport.process_identity(os.getpid()))
    coordinator = _coordinator(directory, relaunched)
    handed_over: list[tuple[str, str]] = []

    async def request(target: dict[str, Any], operation: str, _arguments: dict) -> dict:
        handed_over.append((target["generation"], operation))
        return {}

    monkeypatch.setattr(service_rpc, "request", request)
    accepted = await coordinator.publish()
    assert accepted["generation"] == "new"
    current = GenerationRegistry(directory).current()
    assert current is not None and (current["generation"], current["process"]) == ("new", relaunched["process"])
    assert handed_over == [("old", "prepare_update"), ("old", "commit_update")]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_superseded_generation_stays_reachable_until_publisher_commits(tmp_path: pathlib.Path) -> None:
    """会話を持たない旧世代は、新世代が受付を公開してから確定を送るまでの間に退出せず、確定を受け取ってから退出する。

    Windowsのファイルロックは競合時に1秒待ってから取り直すため、旧世代は公開と確定の送信の間にも退出を判定する。
    2026-11-20、版の更新直後の起動確認で、旧世代が確定を受け取らずに退出し、新世代の公開が`EOFError`で失敗した。
    """
    caller = runtime_environment.CallerContext(dict(os.environ), tmp_path)
    directory = service_transport.namespace(caller)
    registry = GenerationRegistry(directory)
    registry.reserve("old", tmp_path / "old", "1.0.0")
    stop = asyncio.Event()
    serving = asyncio.create_task(service.run(directory, "old", stop=stop))
    # 受付先のプロセスが自身と同じなら旧世代は自身を受付先と扱うため、公開元は別の生存するプロセスとする。
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as publisher:
        try:
            async with asyncio.timeout(10):
                while not (directory / "old.json").exists():
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
            old = json.loads((directory / "old.json").read_text(encoding="utf-8"))
            registry.publish("old", endpoint=old["endpoint"], process=old["process"], server_instance=old["server_instance"])
            registry.reserve("new", tmp_path / "new", "1.0.1")
            new = _target(
                directory,
                "new",
                registry.generations()["new"]["epoch"],
                service_transport.process_identity(publisher.pid),
            )
            await service_rpc.request(old, "prepare_update", {"token": "token", "target": new}, timeout=10)
            registry.publish("new", endpoint=new["endpoint"], process=new["process"], server_instance=new["server_instance"])
            await asyncio.sleep(1.5)
            assert not serving.done(), "旧世代が確定を受け取る前に退出した"
            await service_rpc.request(old, "commit_update", {"token": "token", "current": registry.current()}, timeout=10)
            async with asyncio.timeout(10):
                await serving
        finally:
            stop.set()
            await asyncio.gather(serving, return_exceptions=True)
            publisher.kill()


@pytest.mark.parametrize("previous_exits", [True, False])
@pytest.mark.asyncio
async def test_publication_settles_only_when_previous_generation_exits_without_commit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, previous_exits: bool
) -> None:
    """確定の接続を閉じた旧世代が終了すれば公開を完了し、生存したままなら静止が残るため公開の失敗を返す。

    修正前の版の旧世代は確定の到着を待たずに退出し、確定の接続を閉じる。旧世代の終了は切断より遅れて観測される。
    """
    directory = tmp_path / "agent-toolkit" / "services" / "agents-server"
    registry = GenerationRegistry(directory)
    # Windowsは終了したプロセスもhandleが残る間は開けるため、終了させた後で起動した側のhandleを手放す。
    processes = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])]  # pylint: disable=consider-using-with
    old = _target(directory, "old", 1, service_transport.process_identity(processes[0].pid))
    registry.reserve("old", tmp_path / "old", "1.0.0")
    registry.publish("old", endpoint=old["endpoint"], process=old["process"], server_instance=old["server_instance"])
    registry.reserve("new", tmp_path / "new", "1.0.1")
    coordinator = _coordinator(directory, _target(directory, "new", 2, service_transport.process_identity(os.getpid())))

    def exit_previous() -> None:
        process = processes.pop()
        process.kill()
        process.wait()

    async def request(_previous: dict[str, Any], operation: str, _arguments: dict) -> dict:
        if operation == "commit_update":
            if previous_exits:
                asyncio.get_running_loop().call_later(0.3, exit_previous)
            raise EOFError
        return {}

    monkeypatch.setattr(service_rpc, "request", request)
    try:
        if previous_exits:
            accepted = await coordinator.publish()
            assert accepted["generation"] == "new"
        else:
            monkeypatch.setattr(service_coordinator, "_PREVIOUS_EXIT_SECONDS", 0.5)
            with pytest.raises(EOFError):
                await coordinator.publish()
        current = registry.current()
        assert current is not None and current["generation"] == "new"
    finally:
        for process in processes:
            process.kill()
            process.wait()


def test_exited_candidate_is_not_published(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """起動確認の終了後に生存していない候補の世代は公開せず、生存する世代を受付先に保つ。"""
    directory, _old, new = _superseded(tmp_path, publish_new=False)
    (directory / "new.json").write_text(json.dumps(new), encoding="utf-8")
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("new", "1.0.1"))
    request = Mock()
    monkeypatch.setattr(service_rpc, "request_sync", request)
    caller = runtime_environment.CallerContext({}, tmp_path)
    with pytest.raises(RuntimeError, match="生存していないため公開しません"):
        service_forward.publish_service(caller, tmp_path / "new", timeout=5)
    request.assert_not_called()
    current = GenerationRegistry(directory).current()
    assert current is not None and current["generation"] == "old"


def test_publication_fails_and_restores_live_generation_when_published_one_exits(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開の直後に終了した世代を公開成功として扱わず、生存する世代へ受付を戻す。"""
    directory, _old, new = _superseded(tmp_path, publish_new=False)
    live = dict(new, process=service_transport.process_identity(os.getpid()))
    (directory / "new.json").write_text(json.dumps(live), encoding="utf-8")
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("new", "1.0.1"))

    def publish_then_exit(record: dict[str, Any], operation: str, _arguments: dict, **_kwargs: Any) -> dict[str, Any]:
        assert (record, operation) == (live, "publish")
        return GenerationRegistry(directory).publish(
            "new", endpoint=new["endpoint"], process=new["process"], server_instance=new["server_instance"]
        )

    monkeypatch.setattr(service_rpc, "request_sync", publish_then_exit)
    caller = runtime_environment.CallerContext({}, tmp_path)
    with pytest.raises(RuntimeError, match="公開した受付先の世代が終了しました"):
        service_forward.publish_service(caller, tmp_path / "new", timeout=5)
    current = GenerationRegistry(directory).current()
    assert current is not None and current["generation"] == "old"


def test_publication_interrupted_by_candidate_exit_restores_live_generation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候補が受付先を公開した後、応答を返す前に終了しても、生存する世代へ受付を戻してから失敗を返す。"""
    directory, _old, new = _superseded(tmp_path, publish_new=False)
    live = dict(new, process=service_transport.process_identity(os.getpid()))
    (directory / "new.json").write_text(json.dumps(live), encoding="utf-8")
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("new", "1.0.1"))

    def publish_then_exit(_record: dict[str, Any], _operation: str, _arguments: dict, **_kwargs: Any) -> dict[str, Any]:
        GenerationRegistry(directory).publish(
            "new", endpoint=new["endpoint"], process=new["process"], server_instance=new["server_instance"]
        )
        raise EOFError

    monkeypatch.setattr(service_rpc, "request_sync", publish_then_exit)
    caller = runtime_environment.CallerContext({}, tmp_path)
    with pytest.raises(EOFError):
        service_forward.publish_service(caller, tmp_path / "new", timeout=5)
    current = GenerationRegistry(directory).current()
    assert current is not None and current["generation"] == "old"
