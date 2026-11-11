"""Codexのplugin_root_mcpを検証する。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import anyio
import psutil
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent_toolkit._common import plugin_environment, process_identity
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.codex_plugin_support import (
    _REAL_MCP_PREPARE_TIMEOUT,
    _cleanup_entry,
    _real_mcp_environment,
    _wait_mcp_processes,
)
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


def test_wait_mcp_processes_ignores_stale_alive(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, host_environ: Callable[[], dict[str, str]]
) -> None:
    """待機がaliveとして返したidentityも、判定時に終了済みなら残存として扱わない。"""
    with late_descendant.held_process(tmp_path, env=host_environ()) as held:
        process = psutil.Process(held.pid)

        def _stale_wait(processes: list[psutil.Process], timeout: float) -> tuple[list[psutil.Process], list[psutil.Process]]:
            # 最終pollの後、返却より前に終了した状態を、所有する実プロセスの終了とreapで作る。
            assert timeout == 3
            held.kill()
            held.wait(timeout=10)
            return [], list(processes)

        monkeypatch.setattr(psutil, "wait_procs", _stale_wait)
        _wait_mcp_processes({process.pid: process})
        assert not process.is_running()


def test_wait_mcp_processes_fails_for_living_process(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """生存を続ける所有identityは失敗とし、上限値・PID・状態を診断へ残す。"""
    with late_descendant.held_process(tmp_path, env=host_environ()) as held:
        process = psutil.Process(held.pid)
        with pytest.raises(AssertionError) as raised:
            _wait_mcp_processes({process.pid: process}, timeout=0.2)
        assert process.is_running()
    message = str(raised.value)
    assert "timeout=0.2" in message
    assert f"pid={process.pid}" in message
    assert "status=" in message
    assert "スタックトレースで_wait_mcp_processesの呼び出し元を確認" in message
    assert "そのテストのClientSessionとstdio_clientの終了処理" in message
    assert "stdio_client" in message


@pytest.mark.timeout(180)
@pytest.mark.asyncio
@pytest.mark.usefixtures("share_package_caches")
async def test_stable_plugin_root_shared_across_processes(
    tmp_path: pathlib.Path,
    tmp_path_factory: pytest.TempPathFactory,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """外側2台と保持先起動の実MCPが配布物を共有し、元版の更新・削除後も応答して終了後に回収できる。"""
    plugin = pathlib.Path(__file__).resolve().parents[2]
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    source = tmp_path / "distribution" / version
    shutil.copytree(plugin, source, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git", "node_modules"))
    version_file = pathlib.Path("skills/managed-temp/SKILL.md")
    original = (source / version_file).read_bytes()
    environment = _real_mcp_environment(tmp_path, tmp_path_factory, host_environ)
    state_base = tmp_path / "runtime-state"
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None
    prepare_plugin_environment(source, environment, timeout=_REAL_MCP_PREPARE_TIMEOUT)
    dependency_environment = plugin_environment.project_environment(source, environment)
    lease_directory = state_base / "agent-toolkit/plugin-root-leases"
    clients: list[ClientSession] = []
    owned: dict[int, psutil.Process] = {}
    logs: list[pathlib.Path] = []
    observed: list[dict[str, Any]] = []
    area: pathlib.Path | None = None
    expected_tools = {"start", "send_message", "kill", "stop", "list", "show"}
    try:
        # 配布元と保持先の2環境のcold導入に、実MCP操作の予算を加える。
        with contextlib.ExitStack() as files, anyio.fail_after(135):
            async with contextlib.AsyncExitStack() as stack:
                for index in range(3):
                    root = source if index < 2 else area / version if area is not None else source
                    assert plugin_environment.project_environment(root, environment) == dependency_environment
                    command = plugin_environment.launch_command(root, pathlib.Path("agent_toolkit/agents_server_mcp.py"))
                    log = tmp_path / f"mcp-{index}.stderr"
                    logs.append(log)
                    error_output = files.enter_context(log.open("w", encoding="utf-8"))
                    streams = await stack.enter_async_context(
                        stdio_client(StdioServerParameters(command=uv, args=command[1:], env=environment), errlog=error_output)
                    )
                    client = await stack.enter_async_context(ClientSession(*streams))
                    initialized = await client.initialize()
                    assert initialized.server_info.name == "agents_server"
                    tools = {tool.name for tool in (await client.list_tools()).tools}
                    assert tools == expected_tools
                    clients.append(client)
                    records = [json.loads(path.read_text(encoding="utf-8")) for path in lease_directory.glob("*.json")]
                    leases = [record for record in records if "path" in record]
                    assert len(records) == len(leases)
                    assert len(leases) == index + 1
                    areas = {pathlib.Path(record["path"]) for record in leases}
                    assert len(areas) == 1
                    area = areas.pop()
                    held = area / version
                    marker = json.loads((area / ".agent-toolkit-plugin-root.json").read_text(encoding="utf-8"))
                    assert marker == {"source": str(source), "version": version, "root_name": version}
                    assert list(area.parent.glob("agents-server-plugin-root-*")) == [area]
                    assert (held / version_file).read_bytes() == original
                    for record in leases:
                        process = psutil.Process(record["pid"])
                        assert process_identity.matches(process, record["created_at"], record.get("start_id"))
                        ancestors = process_identity.parents(process)
                        parent_index = next(i for i, parent in enumerate(ancestors) if parent.pid == os.getpid())
                        for member in (process, *ancestors[:parent_index]):
                            owned[member.pid] = member
                    observed.append({"server": index, "tools": sorted(tools), "leases": leases, "marker": marker})
                    if index == 1:
                        (source / version_file).write_text("更新された配布元", encoding="utf-8")
                        assert (held / version_file).read_bytes() == original
                        for client in clients:
                            assert {tool.name for tool in (await client.list_tools()).tools} == expected_tools
                        shutil.rmtree(source)
                        assert not source.exists()
                        for target in (area, held):
                            protected = subprocess.run(
                                [sys.executable, str(_cleanup_entry(tmp_path)), str(target)],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                env=environment,
                                timeout=15,
                                check=False,
                            )
                            assert protected.returncode == 2, protected.stderr
                for client in clients:
                    assert {tool.name for tool in (await client.list_tools()).tools} == expected_tools
                    listed = await client.call_tool("list", {})
                    assert not listed.is_error
                    assert listed.structured_content is not None and listed.structured_content["sessions"] == []
                assert area is not None
                assert (area / version / version_file).read_bytes() == original
    finally:
        _wait_mcp_processes(owned)
        for log in logs:
            print(f"S02 MCP診断 {log.name}:\n" + log.read_text(encoding="utf-8"))
    assert area is not None
    cleaned = subprocess.run(
        [sys.executable, str(_cleanup_entry(tmp_path)), str(area)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=15,
        check=False,
    )
    assert cleaned.returncode == 0, cleaned.stderr
    assert not area.exists()
    print("S02: " + json.dumps({"observed": observed, "terminated_processes": sorted(owned), "root_removed": str(area)}))
