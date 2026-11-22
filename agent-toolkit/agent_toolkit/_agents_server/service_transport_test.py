"""OS由来の接続相手を、環境や操作本文の配送前に検収する。"""

# 生存判定の呼び出し元を網羅するため、各呼び出し元の内部状態を直接組み立てる。
# pylint: disable=protected-access

import ast
import asyncio
import contextlib
import ctypes
import json
import os
import pathlib
import socket
import sys
from collections.abc import Awaitable, Callable
from multiprocessing import connection
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit._agents_server import (
    mcp_tools,
    service_context,
    service_forward,
    service_io,
    service_rpc,
    service_transport,
    windows_service_transport,
)
from agent_toolkit._agents_server.service_coordinator_test import _coordinator
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_projection import ProjectionHub
from agent_toolkit._common import runtime_environment


def test_windows_owner_token_selects_distinct_registry_and_pipe(monkeypatch, tmp_path):
    caller = runtime_environment.CallerContext(
        {"USERPROFILE": str(tmp_path), "LOCALAPPDATA": str(tmp_path), "HOME": str(tmp_path)}, tmp_path
    )
    monkeypatch.setattr(service_transport, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(windows_service_transport, "current_process_elevated", lambda: False)
    standard = service_transport.namespace(caller)
    monkeypatch.setattr(windows_service_transport, "current_process_elevated", lambda: True)
    elevated = service_transport.namespace(caller)
    assert standard.name == "agents-server-standard" and elevated.name == "agents-server-elevated"
    assert standard.parent == elevated.parent
    assert standard.parent.parent == elevated.parent.parent == service_transport.namespace(caller).parent.parent
    assert service_transport.endpoint(standard, "G1") != service_transport.endpoint(elevated, "G1")


@pytest.mark.parametrize("pid,code,missing", [(123, 87, True), (123, 5, False), (0, 87, False)])
def test_windows_process_identity_distinguishes_missing_process_from_api_failure(monkeypatch, pid, code, missing):
    """消滅PIDだけを再起動可能とし、拒否と不正PIDを消滅へ変換しない。"""
    error = OSError("injected OpenProcess failure")
    monkeypatch.setattr(windows_service_transport, "_api", lambda: (SimpleNamespace(OpenProcess=lambda *_: None), None))
    monkeypatch.setattr(
        windows_service_transport, "_windows", SimpleNamespace(WinError=lambda _: error, get_last_error=lambda: code)
    )
    with pytest.raises(ProcessLookupError if missing else OSError) as caught:
        windows_service_transport.process_identity(pid)
    if missing:
        assert caught.value.__cause__ is error
    else:
        assert caught.value is error


@pytest.mark.skipif(sys.platform != "linux", reason="実SO_PEERCREDが必要")
@pytest.mark.parametrize("mismatch", ["account", "process"])
def test_peer_identity_rejects_wrong_account_or_process_before_payload(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    first, second = socket.socketpair()
    with first, second, connection.Connection(os.dup(first.fileno())) as peer:
        expected = service_transport.process_identity(os.getpid())
        with monkeypatch.context() as changed:
            if mismatch == "account":
                other_uid = os.geteuid() + 1
                changed.setattr(service_transport.os, "geteuid", lambda: other_uid)
            else:
                expected = "different-process-generation"
            with pytest.raises(PermissionError):
                service_transport.authenticate(peer, server=False, expected_process=expected)
        assert (
            service_transport.authenticate(
                peer,
                server=False,
                expected_process=service_transport.process_identity(os.getpid()),
            )
            == os.getpid()
        )
        assert not peer.poll()


def _denied(_pid: int) -> str:
    raise PermissionError(13, "Access is denied", None, 5)


@pytest.mark.parametrize("listed_offset,alive", [(0, True), (1, False), (None, False)])
def test_windows_process_alive_matches_unopenable_process_by_process_list(
    monkeypatch: pytest.MonkeyPatch, listed_offset: int | None, alive: bool
) -> None:
    """開けない同じアカウントの世代をプロセス一覧の作成時刻で生存と判定し、PID再利用の別プロセスと、一覧に無い世代は終了とする。

    2026-11-18、Windowsで非昇格の対話ログオンのtokenからSSHログオンの昇格プロセスを開くと、アクセス拒否（5）になった。
    """
    created = 134_000_000_000_000_000
    monkeypatch.setattr(service_transport, "process_identity", _denied)
    monkeypatch.setattr(service_transport, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        windows_service_transport,
        "_listed_create_time",
        lambda pid: None if listed_offset is None or pid != 4242 else created + listed_offset,
    )
    assert service_transport.process_alive(f"4242:{created}") is alive


def test_windows_process_list_entries_are_read_by_pid() -> None:
    """プロセス一覧のバッファを、可変長の各項目を辿って対象PIDの作成時刻まで読む。"""
    entry_size = ctypes.sizeof(windows_service_transport._SystemProcessInformation)
    entries = [(4, 111), (4242, 222), (99, 333)]
    snapshot = ctypes.create_string_buffer(entry_size * len(entries) + 7)
    for index, (pid, created) in enumerate(entries):
        entry = windows_service_transport._SystemProcessInformation.from_buffer(snapshot, index * entry_size)
        entry.UniqueProcessId, entry.CreateTime = pid, created
        entry.NextEntryOffset = entry_size if index + 1 < len(entries) else 0
    assert windows_service_transport._find_create_time(snapshot, 4242) == 222
    assert windows_service_transport._find_create_time(snapshot, 99) == 333
    assert windows_service_transport._find_create_time(snapshot, 5) is None


@pytest.mark.parametrize("reports_needed", [True, False], ids=["needed", "no-needed"])
@pytest.mark.parametrize("status", [0xC0000004, 0xC0000023], ids=["info-length-mismatch", "buffer-too-small"])
def test_windows_process_list_grows_buffer_until_listing_fits(
    monkeypatch: pytest.MonkeyPatch, status: int, reports_needed: bool
) -> None:
    """一覧が最初に確保したバッファに収まらない場合、不足の通知の種類と必要量の返却の有無によらず、広げて取り直して読む。"""
    required = 3 << 20
    sizes: list[int] = []

    def query(_information_class: int, snapshot: Any, size: int, needed: Any) -> int:
        sizes.append(size)
        assert len(sizes) <= 8, f"バッファが広がらない: {sizes}"
        if size < required:
            needed._obj.value = required if reports_needed else 0
            return status - (1 << 32)
        entry = windows_service_transport._SystemProcessInformation.from_buffer(snapshot)
        entry.UniqueProcessId, entry.CreateTime = 4242, 222
        return 0

    monkeypatch.setattr(
        windows_service_transport,
        "_windows",
        SimpleNamespace(WinDLL=lambda _name: SimpleNamespace(NtQuerySystemInformation=query)),
    )
    assert windows_service_transport._listed_create_time(4242) == 222


def test_stdlib_only_forwarding_side_does_not_import_psutil() -> None:
    """標準ライブラリだけで動く転送側が使う生存判定のモジュールは、psutilを読み込まない。

    転送側が別ログオンの昇格世代に当たると、依存環境の外でpsutilを読み込んで失敗した。
    """
    for module in (service_transport, windows_service_transport):
        tree = ast.parse(pathlib.Path(module.__file__ or "").read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
        } | {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        assert "psutil" not in imported, module.__name__


# 終了した世代の起動単位。PIDは別アカウントのプロセスが再利用し、照会がアクセス拒否で失敗する。
_REUSED_PID = 4242
_REUSED = f"{_REUSED_PID}:1"


def _deny_reused_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    real = service_transport.process_identity

    def identity(pid: int) -> str:
        if pid == _REUSED_PID:
            # WindowsのOpenProcessがERROR_ACCESS_DENIEDで失敗した場合と同じ例外。
            raise PermissionError(13, "Access is denied", None, 5)
        return real(pid)

    monkeypatch.setattr(service_transport, "process_identity", identity)


async def _publish(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """現行受付の世代が終了していれば、その世代へ引継ぎを要求せずに受付を公開する。"""
    coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
    coordinator.target.update(endpoint="G3", server_instance="instance-G3")
    monkeypatch.setattr(coordinator.registry, "current", lambda: {"generation": "G0", "epoch": 1, "process": _REUSED})
    accepted = {"generation": coordinator.generation, "epoch": 3}
    monkeypatch.setattr(coordinator.registry, "publish", lambda *_args, **_kwargs: accepted)
    coordinator.projections = cast(Any, SimpleNamespace(activate=AsyncMock(), seed=AsyncMock()))
    request = AsyncMock()
    monkeypatch.setattr(service_rpc, "request", request)
    assert await coordinator.publish() is accepted
    request.assert_not_awaited()


async def _commit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """確定が届かず、退役を記録しないまま終了した旧世代は、退役を待たずに公開の失敗として返す。"""
    coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
    registry = GenerationRegistry(tmp_path)
    registry.reserve("G0", tmp_path, "1.0.0")
    registry.publish("G0", endpoint="G0", process=_REUSED)
    monkeypatch.setattr(service_rpc, "request", AsyncMock(side_effect=EOFError))
    async with asyncio.timeout(5):
        with pytest.raises(RuntimeError, match="正常な退役の記録"):
            await coordinator._commit({"generation": "G0", "epoch": 1, "process": _REUSED}, "token")


def _freezable(coordinator: Any) -> None:
    coordinator.projections = cast(Any, SimpleNamespace(freeze=AsyncMock(), thaw=AsyncMock(), inventory=dict))
    coordinator.registrations = cast(Any, SimpleNamespace(freeze=AsyncMock(), thaw=Mock()))


async def _tick(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """終了した世代から受け取った移譲の記録を、周期処理で回収する。"""
    coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
    coordinator._received["received"] = ("transfer", {"process": _REUSED})
    coordinator._incoming["incoming"] = {"source": {"process": _REUSED}}
    await coordinator.tick()
    assert not coordinator._received
    assert not coordinator._incoming


async def _reclaim_unadopted(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """元世代が終了してPIDが再利用されても、closeの応答を受け取った資源だけ回収済みとする。"""
    for acknowledged in (True, False):
        coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
        coordinator.managers.conversations.clear()
        coordinator._pending_adoptions["expired"] = {
            "parent": "parent",
            "resource": {"identifier": "raw", "origin": {"generation": "G0", "process": _REUSED}},
        }
        release = None if acknowledged else EOFError()
        monkeypatch.setattr(service_rpc, "request", AsyncMock(side_effect=[release, ConnectionRefusedError()]))
        await coordinator.tick()
        assert coordinator.holds_handover() == (not acknowledged)


async def _foreign_sessions(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """親会話のsession一覧を失敗させず、終了した世代へ照会しない。"""
    coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
    monkeypatch.setattr(coordinator.registry, "generations", lambda: {"G2": {"ready": True}})
    monkeypatch.setattr(service_rpc, "discovery", lambda *_: {"process": _REUSED, "server_instance": "instance-G2"})
    request = AsyncMock()
    monkeypatch.setattr(service_rpc, "request", request)
    assert await coordinator._foreign_sessions("parent", include_terminated=False) == {"sessions": [], "unreachable": []}
    request.assert_not_awaited()


async def _owns(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """所有世代が終了していれば、配送の失敗を返さず自世代の操作（登録簿による所有者喪失の判定）へ渡す。"""
    coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
    manager = coordinator.managers.conversations["parent"].manager

    @contextlib.asynccontextmanager
    async def scope(_params: CallToolRequestParams):
        yield manager

    async def owner(_identifier: str) -> dict:
        return {"generation": "G0", "process": _REUSED, "server_instance": "instance-G0"}

    monkeypatch.setattr(coordinator.managers, "scope", scope)
    monkeypatch.setattr(coordinator, "_owner", owner)
    monkeypatch.setattr(service_rpc, "request", AsyncMock(side_effect=ConnectionRefusedError("所有世代は終了済み")))
    local = AsyncMock(return_value=CallToolResult(content=[]))
    monkeypatch.setattr(mcp_tools.mcp, "_call_registered_tool", local)
    params = CallToolRequestParams(name="show", arguments={"session_id": "other"})
    with runtime_environment.bind(runtime_environment.CallerContext({}, tmp_path)):
        assert await coordinator.call_tool(None, params) is local.return_value
    local.assert_awaited_once()


async def _generations(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """listの世代一覧を失敗させず、終了した世代を除く。"""
    del monkeypatch
    hub = ProjectionHub("new", tmp_path / "services/agents-server")
    for name, process in (("reused", _REUSED), ("new", service_transport.process_identity(os.getpid()))):
        hub.registry.reserve(name, tmp_path, "1.0.0")
        hub.registry.publish(name, endpoint=name, process=process, server_instance=f"instance-{name}")
    managers = service_context.ServiceManagers(state_root=tmp_path, projections=hub)
    assert [item["generation"] for item in await managers.generations()] == ["new"]


async def _close(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """資源を所有した世代が終了していれば、資源の終了処理を失敗させない。"""
    del tmp_path

    class _Gone:
        origin = {"generation": "G1", "process": _REUSED}

        async def operation(self, *_args: Any, **_kwargs: Any) -> None:
            raise ConnectionRefusedError("所有世代は終了済み")

    async def unexpected(*_args: Any) -> None:
        raise AssertionError("呼ばれない")

    client = service_io.CodexResource(cast(Any, _Gone()), unexpected, unexpected, unexpected)
    monkeypatch.setattr(service_io.psutil, "pid_exists", lambda _pid: False)
    assert not await client.close()


async def _ensure_service(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ世代の発見記録のプロセスが終了していれば、その記録へ接続せず新しいプロセスを起動する。"""
    directory = tmp_path / "services/agents-server"
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("G1", "1.0.0"))
    GenerationRegistry(directory).reserve("G1", tmp_path, "1.0.0")
    (directory / "G1.json").write_text(json.dumps({"generation": "G1", "process": _REUSED}), encoding="utf-8")
    request = AsyncMock()
    monkeypatch.setattr(service_rpc, "request_sync", request)

    class _Launch(Exception):
        """新しいプロセスの起動準備へ進んだ。"""

    def launch(*_args: Any) -> None:
        raise _Launch

    monkeypatch.setattr(service_forward.plugin_environment, "prepared_python", launch)
    with pytest.raises(_Launch):
        service_forward.ensure_service(runtime_environment.CallerContext({}, tmp_path), tmp_path)
    request.assert_not_called()


async def _supervise(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保存を静止させた公開元の世代が終了していれば、確定の通知を待たずに静止を解く。"""
    coordinator, _session, _backend = _coordinator(tmp_path, monkeypatch)
    _freezable(coordinator)
    coordinator.managers.conversations.clear()
    await coordinator.prepare("token", {"generation": "G4", "epoch": 4, "process": _REUSED})
    async with asyncio.timeout(5), coordinator.gate.admit(new_turn=True):
        pass
    coordinator.registrations.thaw.assert_called_once_with("token")


async def _restore(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """受付先の世代が終了していれば、生存する世代へ受付を戻す。"""
    del monkeypatch
    registry = GenerationRegistry(tmp_path)
    for name, process in (("live", service_transport.process_identity(os.getpid())), ("reused", _REUSED)):
        registry.reserve(name, tmp_path, "1.0.0")
        registry.publish(name, endpoint=name, process=process)
    restored = registry.restore()
    assert restored is not None and restored["generation"] == "live"


async def _publish_service(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """候補の世代が終了していれば、その世代へ公開を要求しない。"""
    directory = tmp_path / "services/agents-server"
    monkeypatch.setattr(service_transport, "namespace", lambda _caller: directory)
    monkeypatch.setattr(service_forward, "code_identity", lambda _root, _directory: ("G1", "1.0.0"))
    GenerationRegistry(directory).reserve("G1", tmp_path, "1.0.0")
    (directory / "G1.json").write_text(json.dumps({"generation": "G1", "process": _REUSED}), encoding="utf-8")
    request = AsyncMock()
    monkeypatch.setattr(service_rpc, "request_sync", request)
    with pytest.raises(RuntimeError, match="生存していないため公開しません"):
        service_forward.publish_service(runtime_environment.CallerContext({}, tmp_path), tmp_path, timeout=5)
    request.assert_not_called()


# 起動単位の生存判定を呼ぶ箇所ごとの、照会を拒否された終了済み世代の場面。
_DENIED_GENERATION_SCENES: dict[str, Callable[[pathlib.Path, pytest.MonkeyPatch], Awaitable[None]]] = {
    "service_coordinator.ServiceCoordinator.publish": _publish,
    "service_coordinator.ServiceCoordinator._commit": _commit,
    "service_coordinator.ServiceCoordinator.tick": _tick,
    "service_coordinator.ServiceCoordinator._reclaim_unadopted": _reclaim_unadopted,
    "service_session_control.ServiceSessionControl._foreign_sessions": _foreign_sessions,
    "service_session_control.ServiceSessionControl._owns": _owns,
    "service_coordinator.ServiceCoordinator._supervise": _supervise,
    "service_context.ServiceManagers.generations": _generations,
    "service_io.CodexResource.close": _close,
    "service_forward.ensure_service": _ensure_service,
    "service_forward.publish_service": _publish_service,
    "service_generations.GenerationRegistry.restore": _restore,
}


@pytest.mark.parametrize("caller", sorted(_DENIED_GENERATION_SCENES))
@pytest.mark.asyncio
async def test_callers_treat_denied_query_of_reused_pid_as_exited_generation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caller: str
) -> None:
    """終了した世代のPIDを別アカウントのプロセスが再利用し照会が拒否されても、各呼び出し元は終了した世代として扱う。

    2026-11-18、拒否の例外が生存判定から素通しし、list・show・kill・send_messageが「Access is denied」で終わった。
    """
    _deny_reused_pid(monkeypatch)
    await _DENIED_GENERATION_SCENES[caller](tmp_path, monkeypatch)


class _LivenessCalls(ast.NodeVisitor):
    """管理サービスのモジュールから、他プロセスの起動単位を照会する呼び出しの所在を集める。"""

    def __init__(self, module: str) -> None:
        self.scope = [module]
        self.found: set[str] = set()

    def _enter(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "service_transport"
            and function.attr in {"process_alive", "process_identity"}
            # 自身の起動単位の取得は生存判定ではない。
            and not (function.attr == "process_identity" and ast.unparse(node.args[0]) == "os.getpid()")
        ):
            self.found.add(".".join(self.scope))
        self.generic_visit(node)


@pytest.mark.cpu_limit(
    1.5,
    reason="同じパッケージの全モジュールを構文解析して呼び出し元を集める（ローカル実測0.70秒、CI実測0.58秒）。",
)
def test_denied_generation_scenes_cover_every_liveness_caller() -> None:
    """他世代の生存を判定する呼び出し元を追加したら、照会を拒否された場面も上の表へ加える。"""
    found: set[str] = set()
    for path in sorted(pathlib.Path(service_transport.__file__).parent.glob("*.py")):
        if not path.name.endswith("_test.py"):
            visitor = _LivenessCalls(path.stem)
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            found |= visitor.found
    assert found == set(_DENIED_GENERATION_SCENES)


@pytest.mark.parametrize("alive", [False, True])
def test_connect_only_converts_permission_error_for_exited_generation(monkeypatch: pytest.MonkeyPatch, alive: bool) -> None:
    """接続時の拒否でも、世代の生存中は認証失敗のまま返す。"""
    client = Mock(side_effect=PermissionError("接続拒否"))
    monkeypatch.setattr(service_transport.connection, "Client", client)
    monkeypatch.setattr(service_transport, "process_alive", lambda _identity: alive)
    expected = PermissionError if alive else FileNotFoundError
    with pytest.raises(expected), service_transport.connect("endpoint", "42:1"):
        pytest.fail("操作を実行してはならない")
