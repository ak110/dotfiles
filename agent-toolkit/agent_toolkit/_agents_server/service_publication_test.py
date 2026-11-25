# 世代間の受付と静止の内部状態を直接組み立てて検証する。
# pylint: disable=protected-access

import asyncio
import contextlib
import dataclasses
import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
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
from agent_toolkit._agents_server.service_publication_test_child import PREVIOUS_GENERATION
from agent_toolkit._agents_server.service_registry import RegistryForwarder
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._common import registry_forwarding, runtime_environment, session_registry

# 終了した受付先への接続の失敗はOSごとに例外が異なる（Windowsの名前付きパイプは`FileNotFoundError`）ため、Windowsでも確かめる。
pytestmark = pytest.mark.windows


def _exited_process() -> str:
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


@pytest.mark.asyncio
async def test_supervisor_restores_acceptance_with_all_workers_waiting_on_registration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, old, new = _superseded(tmp_path, publish_new=False)
    coordinator = _coordinator(directory, old)
    registrations = coordinator.registrations = RegistryForwarder("old", directory)
    exited = False
    monkeypatch.setattr(service_transport, "process_alive", lambda process: process == old["process"] or not exited)
    monkeypatch.setattr(service_coordinator, "_PUBLISHER_PROBE_SECONDS", 0.01)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
    waiting: asyncio.Queue[None] = asyncio.Queue()
    wait = registrations._condition.wait

    def waiting_on_freeze() -> bool:
        loop.call_soon_threadsafe(waiting.put_nowait, None)
        return wait()

    monkeypatch.setattr(registrations._condition, "wait", waiting_on_freeze)
    await coordinator.prepare("token", new)
    released = coordinator._updates["token"].released
    aliases = ["first", "second"]
    with registry_forwarding.bind(registrations.forward):
        saves = [asyncio.create_task(asyncio.to_thread(shared_roots.write_root_alias, alias, "root")) for alias in aliases]
    try:
        async with asyncio.timeout(5):
            for _ in saves:
                await waiting.get()
            coordinator.registry.publish("new", endpoint=new["endpoint"], process=new["process"])
            exited = True
            await released.wait()
            assert coordinator.gate.current["generation"] == "old"
            assert coordinator.gate.current["epoch"] == new["epoch"]
            await asyncio.gather(*saves)
        for alias in aliases:
            path = shared_roots.aliases_directory(registrations.state_root) / f"{alias}.json"
            assert json.loads(path.read_text(encoding="utf-8"))["root_session_id"] == "root"
    finally:
        exited = True
        registrations.thaw("token")
        await asyncio.gather(*saves, *coordinator._supervisors)


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
    directory, old, new = _superseded(tmp_path)
    coordinator = _coordinator(directory, old)
    await coordinator.prepare("token", new)
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
    current = GenerationRegistry(directory).current()
    assert current is not None and current["generation"] == "old"


@pytest.mark.asyncio
async def test_late_commit_from_exited_publisher_keeps_acceptance_on_survivor(tmp_path: pathlib.Path) -> None:
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
    directory, _old, new = _superseded(tmp_path)
    relaunched = dict(new, process=service_transport.process_identity(os.getpid()))
    coordinator = _coordinator(directory, relaunched)
    handed_over: list[tuple[str, str]] = []

    async def request(target: dict[str, Any], operation: str, _arguments: dict, **_kwargs: Any) -> dict:
        handed_over.append((target["generation"], operation))
        return {}

    monkeypatch.setattr(service_rpc, "request", request)
    accepted = await coordinator.publish()
    assert accepted["generation"] == "new"
    current = GenerationRegistry(directory).current()
    assert current is not None and (current["generation"], current["process"]) == ("new", relaunched["process"])
    assert handed_over == [("old", "prepare_update"), ("old", "commit_update")]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("disturbance", ["commit_lost_once", "commit_delayed"])
@pytest.mark.asyncio
async def test_idle_previous_generation_retires_even_if_commit_is_lost_or_late(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, disturbance: str
) -> None:
    """会話を持たない旧世代は、確定が一時的な通信の失敗で届かなくても、遅れて届いても退役し、公開は成功する。

    公開元は常駐するため、確定か公開元の終了だけを待つ旧世代は、確定を1回失うと残り続ける。
    Windowsのファイルロックは競合時に1秒待ってから取り直すため、受付の公開から確定の送信までが1秒を超えることがある。
    2026-11-20、版の更新直後の起動確認で、確定より先に退出した旧世代のために新世代の公開が`EOFError`で失敗した。
    """
    caller = runtime_environment.CallerContext(dict(os.environ), tmp_path)
    directory = service_transport.namespace(caller)
    registry = GenerationRegistry(directory)
    registry.reserve("old", tmp_path / "old", "1.0.0")
    stop = asyncio.Event()
    serving = asyncio.create_task(service.run(directory, "old", stop=stop))

    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as resident:
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
                directory, "new", registry.generations()["new"]["epoch"], service_transport.process_identity(resident.pid)
            )
            publisher = _coordinator(directory, new)
            deliver = service_rpc.request
            lost: list[str] = []

            async def request(target: dict[str, Any], operation: str, arguments: dict, **kwargs: Any) -> Any:
                if operation == "commit_update" and disturbance == "commit_lost_once" and not lost:
                    lost.append(operation)
                    raise ConnectionResetError("確定を配送前に失った")
                return await deliver(target, operation, arguments, **kwargs)

            monkeypatch.setattr(service_rpc, "request", request)
            if disturbance == "commit_delayed":
                publisher.projections = Mock(seed=AsyncMock(), activate=lambda: asyncio.sleep(1.5))
            async with asyncio.timeout(30):
                accepted = await publisher.publish()
                await serving
            assert accepted["generation"] == "new"
        finally:
            stop.set()
            await asyncio.gather(serving, return_exceptions=True)
            resident.kill()


@pytest.mark.parametrize("ending", ["confirmed", "collected", "legacy_retired", "killed", "alive"])
@pytest.mark.asyncio
async def test_publication_without_commit_requires_handover_record(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    """旧版の退役記録だけでは成功とせず、受付先に残った移転記録で判定する。"""
    directory = tmp_path / "agent-toolkit" / "services" / "agents-server"
    registry = GenerationRegistry(directory)

    roots = {"old": tmp_path / "old", "new": tmp_path / ("old" if ending == "collected" else "new")}
    for generation, version in (("old", "1.0.0"), ("new", "1.0.1")):
        roots[generation].mkdir(exist_ok=True)
        registry.reserve(generation, roots[generation], version)
    # 終了後も起動側のhandleを保持し、Windowsでプロセスをまだ開ける状態で公開の成否を確かめる。
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE) as process:
        old = _target(directory, "old", 1, service_transport.process_identity(process.pid))
        registry.publish("old", endpoint=old["endpoint"], process=old["process"], server_instance=old["server_instance"])
        coordinator = _coordinator(directory, _target(directory, "new", 2, service_transport.process_identity(os.getpid())))

        async def request(_previous: dict[str, Any], operation: str, _arguments: dict, **_kwargs: Any) -> dict:
            if operation == "commit_update":
                if ending != "alive" and process.poll() is None:
                    if ending in {"confirmed", "collected"}:
                        registry.confirm_handover("old", old["process"])
                    if ending in {"confirmed", "legacy_retired", "collected"}:
                        registry.retire("old", old["process"])
                    process.kill()
                    process.wait()
                raise EOFError
            return {}

        monkeypatch.setattr(service_rpc, "request", request)
        if ending in {"confirmed", "collected"}:
            accepted = await coordinator.publish()
            assert accepted["generation"] == "new"
        elif ending in {"legacy_retired", "killed"}:
            with pytest.raises(RuntimeError, match="会話移転の確定記録"):
                await coordinator.publish()
        else:
            monkeypatch.setattr(service_coordinator, "_COMMIT_WAIT_SECONDS", 0.5)
            with pytest.raises(EOFError):
                await coordinator.publish()
        current = registry.current()
        assert current is not None and current["generation"] == "new"
        assert registry.retired("old", old["process"]) == (ending in {"confirmed", "legacy_retired", "collected"})


def test_handover_record_does_not_survive_destination_process_replacement(tmp_path: pathlib.Path) -> None:
    registry = GenerationRegistry(tmp_path)
    registry.reserve("old", tmp_path, "1.0.0")
    old = registry.publish("old", endpoint="old", process="1:1")
    registry.reserve("new", tmp_path, "1.0.1")
    first = registry.publish("new", endpoint="new", process="2:1")
    registry.confirm_handover("old", old["process"])
    assert registry.handover_confirmed(first, old)
    replacement = registry.publish("new", endpoint="new", process="2:2")
    assert not registry.handover_confirmed(replacement, old)


# 保存またはClaudeの取り込み用設定の組み立てが、会話本体の解放をまたいで1回失敗する世代。
_ADOPTING_GENERATION = """
import asyncio
import pathlib
import sys

from agent_toolkit._agents_server import claude, mcp_tools, service
from agent_toolkit._agents_server.manager import AgentsServerManager

directory, generation, session_id, parent, failure = sys.argv[1:]
failed = False


async def expire_then_fail(manager):
    global failed
    failed = True
    session = manager.sessions[session_id]
    session.retention_deadline = asyncio.get_running_loop().time()
    manager._schedule_session_expiry(session)
    while session_id in manager.sessions:
        await asyncio.sleep(0)
    pathlib.Path("adoption-failed").touch()
    raise OSError("試験で注入したI/Oの失敗")


if failure == "options":
    run = claude.run_file_operation

    async def build_across_deadline(function, *args, **kwargs):
        if function is claude._build_options and not failed:
            await expire_then_fail(mcp_tools._SERVICE_MANAGERS.conversations[parent].manager)
        return await run(function, *args, **kwargs)

    claude.run_file_operation = build_across_deadline
else:
    save = AgentsServerManager.save_control_state

    async def save_across_deadline(self):
        if session_id in self.sessions and not failed:
            await expire_then_fail(self)
        await save(self)

    AgentsServerManager.save_control_state = save_across_deadline

asyncio.run(service.run(pathlib.Path(directory), generation))
"""

_PARENT, _SESSION = "parent-conversation", "child-session"
_OWNED = {"parent": _PARENT, "session_id": _SESSION}
_PREVIOUS_IMPORT_TIMEOUT = 35


def _isolated_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    state_root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    return service_transport.namespace(runtime_environment.CallerContext(dict(os.environ), tmp_path))


async def _wait_for_child_file(path: pathlib.Path, process: subprocess.Popen) -> None:
    while not path.exists():
        assert process.poll() is None, f"{path.name} を出す前に子が終了: {process.returncode}"
        await asyncio.sleep(0.01)


async def _wait_for_discovery(directory: pathlib.Path, generation: str, process: subprocess.Popen) -> dict[str, Any]:
    discovery = directory / f"{generation}.json"
    async with asyncio.timeout(10):
        await _wait_for_child_file(discovery, process)
    return json.loads(discovery.read_text(encoding="utf-8"))


async def _launch_previous_generation(
    tmp_path: pathlib.Path, directory: pathlib.Path, turn: str, processes: list[subprocess.Popen], *, mode: str = "normal"
) -> dict[str, Any]:
    registry = GenerationRegistry(directory)
    registry.reserve("old", tmp_path / "old", "1.0.0")
    script = tmp_path / "previous" / "previous_generation.py"
    script.parent.mkdir()
    script.write_text(PREVIOUS_GENERATION, encoding="utf-8")
    arguments = [str(directory), _PARENT, _SESSION, turn, str(tmp_path / "inject-failure"), str(tmp_path / "finish-turn"), mode]
    # 終了したプロセスのhandleを保持すると生存と判定する環境があるため、呼出元が終了を見届けた後で手放す。
    processes.append(subprocess.Popen([sys.executable, str(script), *arguments], cwd=tmp_path))  # pylint: disable=consider-using-with
    process = processes[-1]
    try:
        async with asyncio.timeout(_PREVIOUS_IMPORT_TIMEOUT):
            await _wait_for_child_file(script.with_suffix(".ready"), process)
    except TimeoutError as error:
        process.kill()
        process.wait(timeout=5)
        processes.pop()
        raise TimeoutError("旧世代の子からimport完了通知が届かなかった") from error
    old = await _wait_for_discovery(directory, "old", process)
    registry.publish("old", endpoint=old["endpoint"], process=old["process"], server_instance=old["server_instance"])
    async with asyncio.timeout(30):
        while not await service_rpc.request(old, "owns", _OWNED):
            await asyncio.sleep(0.05)
    return old


async def _start_generation(directory: pathlib.Path, generation: str, stop: asyncio.Event) -> tuple[asyncio.Task, dict]:
    serving = asyncio.create_task(service.run(directory, generation, stop=stop))
    async with asyncio.timeout(10):
        while not (directory / f"{generation}.json").exists():
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
    return serving, json.loads((directory / f"{generation}.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(os.name == "nt", reason="POSIXのSIGTERMとSIGKILLで旧世代を終了させる")
@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.parametrize("ending", ["moved_then_retired", "sigterm", "exception", "sigkill"])
@pytest.mark.asyncio
async def test_publication_succeeds_only_if_previous_generation_handed_over_its_conversation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    """確定を受け取らずに終了した旧世代について、会話を新世代へ移してから退役していれば公開は成功し、
    会話を持ったまま終了していれば、終了の仕方（SIGTERM・例外・SIGKILL）によらず公開は失敗を返す。

    2026-11-20の独立検証で、会話を持つ旧世代がSIGTERMか例外で終了すると、終了時の後始末が退役を記録して公開が成功を返した。
    """
    directory = _isolated_directory(tmp_path, monkeypatch)
    registry = GenerationRegistry(directory)
    processes: list[subprocess.Popen] = []
    stop = asyncio.Event()
    serving = None
    try:
        turn = "COMPLETED" if ending == "moved_then_retired" else "IN_PROGRESS"
        old = await _launch_previous_generation(tmp_path, directory, turn, processes)
        registry.reserve("new", tmp_path / "new", "1.0.1")
        serving, new = await _start_generation(directory, "new", stop)
        deliver = service_rpc.request

        async def end_previous() -> None:
            process = processes[0]
            if ending == "moved_then_retired":
                await asyncio.to_thread(process.wait, 30)
            else:
                assert await deliver(old, "owns", _OWNED)
                assert not await deliver(new, "owns", _OWNED)
                if ending == "sigterm":
                    process.terminate()
                elif ending == "exception":
                    (tmp_path / "inject-failure").touch()
                else:
                    process.kill()
                await asyncio.to_thread(process.wait, 30)
            processes.pop()

        async def request(target: dict[str, Any], operation: str, arguments: dict, **kwargs: Any) -> Any:
            if operation == "commit_update":
                await end_previous()
            return await deliver(target, operation, arguments, **kwargs)

        monkeypatch.setattr(service_rpc, "request", request)
        async with asyncio.timeout(60):
            if ending == "moved_then_retired":
                accepted = await deliver(new, "publish", {})
                assert accepted["generation"] == "new"
                assert await deliver(new, "owns", _OWNED)
            else:
                with pytest.raises(RuntimeError, match="会話移転の確定記録"):
                    await deliver(new, "publish", {})
        current = registry.current()
        assert current is not None and current["generation"] == "new"
    finally:
        stop.set()
        if serving is not None:
            await asyncio.gather(serving, return_exceptions=True)
        for process in processes:
            process.kill()
            process.wait()


@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.parametrize("disturbance", ["commit_lost_once", "commit_response_lost"])
@pytest.mark.asyncio
async def test_publication_succeeds_after_lost_commit_while_previous_generation_runs_a_turn(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, disturbance: str
) -> None:
    """進行中のturnを持つ旧世代との確定の通信を1回失っても公開は成功し、turnの終了後に会話と登録簿の所有が新世代へ移り、旧世代は退役する。

    確定を配送前に失った場合も、旧世代が受け取った後で応答だけを失った場合も、生存する旧世代へ同じ確定を送り直して済ませる。
    2026-11-20の独立検証で、旧世代は受付先の前進を観測して静止を解き、会話を持ったまま正しく動き続けたが、
    公開元は旧世代の退役だけを待って10秒後に失敗を返した。
    """
    directory = _isolated_directory(tmp_path, monkeypatch)
    registry = GenerationRegistry(directory)
    processes: list[subprocess.Popen] = []
    stop = asyncio.Event()
    serving = None
    try:
        old = await _launch_previous_generation(tmp_path, directory, "IN_PROGRESS", processes)
        registry.reserve("new", tmp_path / "new", "1.0.1")
        serving, new = await _start_generation(directory, "new", stop)
        deliver = service_rpc.request
        lost: list[str] = []

        async def request(target: dict[str, Any], operation: str, arguments: dict, **kwargs: Any) -> Any:
            if operation == "commit_update" and not lost:
                lost.append(operation)
                if disturbance == "commit_response_lost":
                    await deliver(target, operation, arguments, **kwargs)
                raise ConnectionResetError("確定の通信を失った")
            return await deliver(target, operation, arguments, **kwargs)

        monkeypatch.setattr(service_rpc, "request", request)
        async with asyncio.timeout(30):
            accepted = await deliver(new, "publish", {})
        assert accepted["generation"] == "new"
        assert lost == ["commit_update"]
        assert await deliver(old, "owns", _OWNED)
        assert not await deliver(new, "owns", _OWNED)
        (tmp_path / "finish-turn").touch()
        assert await asyncio.to_thread(processes[0].wait, 30) == 0
        processes.pop()
        assert await deliver(new, "owns", _OWNED)
        owner = session_registry.resolve(_SESSION, state_root=directory.parent.parent)
        assert owner.server_instance == new["server_instance"]
        assert registry.retired("old", old["process"])
    finally:
        stop.set()
        if serving is not None:
            await asyncio.gather(serving, return_exceptions=True)
        for process in processes:
            process.kill()
            process.wait()


@contextlib.asynccontextmanager
async def _publisher_with_delayed_commit_response(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[dict]:
    directory = _isolated_directory(tmp_path, monkeypatch)
    processes: list[subprocess.Popen] = []
    stop = asyncio.Event()
    serving = None
    try:
        await _launch_previous_generation(tmp_path, directory, "IN_PROGRESS", processes, mode="delay_response")
        GenerationRegistry(directory).reserve("new", tmp_path / "new", "1.0.1")
        serving, new = await _start_generation(directory, "new", stop)
        yield new
    finally:
        for process in processes:
            process.kill()
            process.wait()
        stop.set()
        if serving is not None:
            await asyncio.gather(serving, return_exceptions=True)


@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.asyncio
async def test_first_commit_response_deadline_releases_publication(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧世代が最初の確定を受理して応答だけが遅れても、公開元は10秒で期限切れを返し、直後の公開を通す。"""
    async with _publisher_with_delayed_commit_response(tmp_path, monkeypatch) as new:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="TimeoutError"):
            await service_rpc.request(new, "publish", {}, timeout=30)
        elapsed = time.monotonic() - started
        assert (await service_rpc.request(new, "publish", {}, timeout=2))["generation"] == "new"
        assert (tmp_path / "commit-accepted").exists()
        assert 9 <= elapsed <= 12


@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.parametrize("delay_response", [True, False], ids=["late-response", "repeated-error"])
@pytest.mark.asyncio
async def test_commit_retry_deadline_returns_first_error_and_releases_publication(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, delay_response: bool
) -> None:
    """確定の応答が止まっても、失敗し続けても、10秒で最初のエラーを返して後続の公開を通す。"""
    async with _publisher_with_delayed_commit_response(tmp_path, monkeypatch) as new:
        deliver = service_rpc.request
        first_failure: asyncio.Future[float] = asyncio.get_running_loop().create_future()

        async def request(target: dict, operation: str, arguments: dict, **kwargs: Any) -> Any:
            if operation == "commit_update":
                if not first_failure.done():
                    first_failure.set_result(asyncio.get_running_loop().time())
                    raise ConnectionResetError("最初の確定を失った")
                if not delay_response:
                    raise EOFError("再送を失った")
            return await deliver(target, operation, arguments, **kwargs)

        monkeypatch.setattr(service_rpc, "request", request)
        async with asyncio.TaskGroup() as group:
            publishing = group.create_task(_failed_publication(new))
            async with asyncio.timeout(30):
                start = await first_failure
            async with asyncio.timeout_at(start + 12):
                assert (await deliver(new, "publish", {}))["generation"] == "new"
                assert await publishing - start >= 9


async def _failed_publication(target: dict) -> float:
    with pytest.raises(RuntimeError, match="ConnectionResetError: 最初の確定を失った"):
        await service_rpc.request(target, "publish", {})
    return asyncio.get_running_loop().time()


@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.asyncio
async def test_generation_whose_adopted_conversation_expired_while_saving_retires_after_next_publication(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会話の保存が保持期限をまたいで失敗しても、次の世代に再開情報を移して退役する。"""
    await _publish_across_expiry(tmp_path, monkeypatch, failure="save")


@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.asyncio
async def test_unadopted_raw_resource_is_closed_before_generations_retire(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _publish_across_expiry(tmp_path, monkeypatch, failure="options")


async def _publish_across_expiry(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, failure: str) -> None:
    directory = _isolated_directory(tmp_path, monkeypatch)
    registry = GenerationRegistry(directory)
    processes: list[subprocess.Popen] = []
    stop = asyncio.Event()
    serving = None
    try:
        old = await _launch_previous_generation(
            tmp_path, directory, "COMPLETED", processes, mode="raw" if failure == "options" else "normal"
        )
        registry.reserve("second", tmp_path / "second", "1.0.1")
        script = tmp_path / "adopting" / "adopting_generation.py"
        script.parent.mkdir()
        script.write_text(_ADOPTING_GENERATION, encoding="utf-8")
        processes.append(
            subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, str(script), str(directory), "second", _SESSION, _PARENT, failure], cwd=tmp_path
            )
        )
        second = await _wait_for_discovery(directory, "second", processes[1])
        async with asyncio.timeout(30):
            assert (await service_rpc.request(second, "publish", {}))["generation"] == "second"
        async with asyncio.timeout(30):
            while not (tmp_path / "adoption-failed").exists():
                await asyncio.sleep(0.05)
        if failure == "save":
            assert await asyncio.to_thread(processes[0].wait, 30) == 0
            processes.pop(0)
            assert registry.retired("old", old["process"])
        registry.reserve("third", tmp_path / "third", "1.0.2")
        serving, third = await _start_generation(directory, "third", stop)
        async with asyncio.timeout(30):
            assert (await service_rpc.request(third, "publish", {}))["generation"] == "third"
        if failure == "options":
            async with asyncio.timeout(30):
                while not (tmp_path / "close-attempted").exists():
                    await asyncio.sleep(0.05)
            for target in (old, second):
                assert (await service_rpc.request(target, "ping", {}))["generation"] == target["generation"]
            (tmp_path / "allow-close").touch()
            assert await asyncio.to_thread(processes[0].wait, 30) == 0
            processes.pop(0)
            assert registry.retired("old", old["process"])
            assert (tmp_path / "raw-closed").is_file()
        assert await asyncio.to_thread(processes[0].wait, 30) == 0
        processes.pop(0)
        assert registry.retired("second", second["process"])
        listed = await service_rpc.request(third, "parent_sessions", {"parent": _PARENT, "include_terminated": True})
        assert [(item["session_id"], item["status"]) for item in listed] == [(_SESSION, "expired")]
    finally:
        stop.set()
        if serving is not None:
            await asyncio.gather(serving, return_exceptions=True)
        for process in processes:
            process.kill()
            process.wait()


def test_exited_candidate_is_not_published(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.mark.skipif(os.name == "nt", reason="POSIXの管理サービスにSIGTERMを送る")
@pytest.mark.usefixtures("agents_server_isolation", "_owner_session_environment")
@pytest.mark.asyncio
async def test_same_version_successor_keeps_conversation_after_shutdown_request(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _isolated_directory(tmp_path, monkeypatch)
    registry = GenerationRegistry(directory)
    processes: list[subprocess.Popen] = []
    try:
        old = await _launch_previous_generation(tmp_path, directory, "IN_PROGRESS", processes)
        registry.reserve("new", tmp_path / "new", "1.0.0")
        command = [sys.executable, "-m", service.__name__, str(directory), "new"]
        with subprocess.Popen(command, stdin=subprocess.PIPE) as successor:
            try:
                new = await _wait_for_discovery(directory, "new", successor)
                accepted = await service_rpc.request(new, "publish", {})
                assert accepted["generation"] == "new"
                assert await service_rpc.request(old, "owns", _OWNED)
                (tmp_path / "finish-turn").touch()
                assert await asyncio.to_thread(processes[0].wait, 30) == 0
                assert await service_rpc.request(new, "owns", _OWNED)

                assert successor.stdin is not None
                successor.stdin.close()
                successor.terminate()
                await asyncio.sleep(1)
                assert successor.poll() is None
                sessions = await service_rpc.request(new, "parent_sessions", {"parent": _PARENT, "include_terminated": False})
                assert [session["session_id"] for session in sessions] == [_SESSION]
                owner = session_registry.resolve(_SESSION, state_root=directory.parent.parent)
                assert owner.server_instance == new["server_instance"]
            finally:
                if successor.poll() is None:
                    successor.kill()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
