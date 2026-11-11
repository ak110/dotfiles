"""Codexのplugin_root_legacyを検証する。"""

from __future__ import annotations

import asyncio
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

from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._common import plugin_environment, process_identity, state_paths
from agent_toolkit._testing.codex_plugin_support import (
    _LEGACY_PLUGIN_ROOT_SOURCE,
    _REAL_MCP_PREPARE_TIMEOUT,
    _cleanup_entry,
    _owned_async_child,
    _real_mcp_environment,
    _wait_mcp_processes,
)
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.windows
@pytest.mark.timeout(180)
@pytest.mark.asyncio
@pytest.mark.usefixtures("share_package_caches")
async def test_legacy_plugin_root_real_mcp_lifecycle(
    tmp_path: pathlib.Path,
    tmp_path_factory: pytest.TempPathFactory,
    host_environ: Callable[[], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧形式の生成fixtureを使う実MCPをOS越しに保護し、全終了後の同じ公開cleanupで回収する。"""
    plugin = pathlib.Path(__file__).resolve().parents[2]
    version = json.loads((plugin / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    source = tmp_path / "legacy-distribution" / version
    shutil.copytree(plugin, source, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git", "node_modules"))
    (source / "agent_toolkit/_agents_server/plugin_root.py").write_text(_LEGACY_PLUGIN_ROOT_SOURCE, encoding="utf-8")
    environment = _real_mcp_environment(tmp_path, tmp_path_factory, host_environ)
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None
    # Windowsでは124パッケージのcopy導入に成功時53秒、失敗時60秒超を観測したため、補助関数の60秒より長い上限を使う。
    prepare_plugin_environment(source, environment, timeout=_REAL_MCP_PREPARE_TIMEOUT)
    command = plugin_environment.launch_command(source, pathlib.Path("agent_toolkit/agents_server_mcp.py"))
    log = tmp_path / "legacy-mcp.stderr"
    log.touch()
    owned: dict[int, psutil.Process] = {}
    observation: dict[str, Any] = {}
    area: pathlib.Path | None = None
    parallel_script = tmp_path / "parallel" / "agent_toolkit" / "agents_server_mcp.py"
    parallel_script.parent.mkdir(parents=True)
    parallel_script.write_text('import sys\nprint("ready", flush=True)\nsys.stdin.read()\n', encoding="utf-8")
    cleanup_script = tmp_path / "fixture_cleanup.py"
    cleanup_script.write_text(
        '''"""公開cleanupへ、この試験が所有するプロセスの実OS情報だけを渡す。"""
import json
import runpy
import sys
import psutil

from agent_toolkit._common import process_identity

identities = json.loads(sys.argv[1])

def fixture_processes(*_args, **_kwargs):
    for entry in identities:
        identity = process_identity.ProcessIdentity(**entry)
        try:
            process = psutil.Process(identity.pid)
            if identity.matches(process):
                yield process
        except psutil.NoSuchProcess:
            continue

psutil.process_iter = fixture_processes
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
''',
        encoding="utf-8",
    )

    def cleanup(target: pathlib.Path, processes: dict[int, psutil.Process] | None = None) -> subprocess.CompletedProcess[str]:
        # 列挙集合だけをfixtureのidentityへ限定する。argv・実行中のOSアカウント・open_filesと公開cleanupは実物を使う。
        identities = [
            process_identity.ProcessIdentity.capture(process).payload()
            for process in (owned if processes is None else processes).values()
        ]
        return subprocess.run(
            [
                sys.executable,
                str(cleanup_script),
                json.dumps(identities),
                str(_cleanup_entry(tmp_path)),
                str(target),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=15,
            check=False,
        )

    async with _owned_async_child(
        sys.executable,
        str(parallel_script),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=environment,
    ) as parallel:
        try:
            assert parallel.stdout is not None
            assert (await asyncio.wait_for(parallel.stdout.readline(), timeout=5)).rstrip() == b"ready"
            # Windowsで約40秒を要したcold導入と、実MCP操作の予算を確保する。
            with log.open("w", encoding="utf-8") as errors, anyio.fail_after(90):
                async with contextlib.AsyncExitStack() as stack:
                    streams = await stack.enter_async_context(
                        stdio_client(StdioServerParameters(command=uv, args=command[1:], env=environment), errlog=errors)
                    )
                    client = await stack.enter_async_context(ClientSession(*streams))
                    initialized = await client.initialize()
                    assert initialized.server_info.name == "agents_server"
                    tools = {tool.name for tool in (await client.list_tools()).tools}
                    assert "start" in tools
                    entrypoint = source / "agent_toolkit/agents_server_mcp.py"
                    servers = []
                    ancestor_pids: set[int] = set()
                    for process in psutil.Process().children(recursive=True):
                        try:
                            arguments = process.cmdline()
                        except psutil.NoSuchProcess:
                            # 列挙後に終了した補助プロセスは、稼働中のMCPの候補から外す。
                            continue
                        if len(arguments) > 1 and pathlib.Path(arguments[1]) == entrypoint:
                            servers.append(process)
                            ancestors = process_identity.parents(process)
                            ancestor_pids.update(parent.pid for parent in ancestors)
                            parent_index = next(i for i, parent in enumerate(ancestors) if parent.pid == os.getpid())
                            for member in (process, *ancestors[:parent_index]):
                                owned[member.pid] = member
                    process_diagnostics = [
                        {
                            "pid": process.pid,
                            "ppid": process.ppid(),
                            "exe": process.exe(),
                            "cmdline": process.cmdline(),
                            "created_at": process.create_time(),
                        }
                        for process in owned.values()
                    ]
                    print("旧形式MCP起動プロセス: " + json.dumps(process_diagnostics, ensure_ascii=False))
                    # venv redirector等の祖先を所有したまま、単一系列の末端を実MCPの観測主体にする。
                    # 複数系列から同じscriptが起動した場合は末端が複数となり失敗する。
                    runtime_processes = [process for process in servers if process.pid not in ancestor_pids]
                    assert len(runtime_processes) == 1, process_diagnostics
                    server = runtime_processes[0]
                    cache_base = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_CACHE_HOME"
                    temp_root = pathlib.Path(environment[cache_base]) / "agent-toolkit/managed-temp"
                    areas = list(temp_root.glob("agents-server-plugin-root-*"))
                    assert len(areas) == 1
                    area = areas[0]
                    assert (area / version / "agent_toolkit/agents_server_mcp.py").is_file()
                    assert not (area / ".agent-toolkit-plugin-root.json").exists()
                    leases = tmp_path / "runtime-state/agent-toolkit/plugin-root-leases"
                    assert not list(leases.glob("*.json"))
                    direct_command = plugin_environment.launch_command(
                        area / version, pathlib.Path("agent_toolkit/agents_server_mcp.py")
                    )
                    direct_streams = await stack.enter_async_context(
                        stdio_client(StdioServerParameters(command=uv, args=direct_command[1:], env=environment), errlog=errors)
                    )
                    direct_client = await stack.enter_async_context(ClientSession(*direct_streams))
                    await direct_client.initialize()
                    direct_servers = []
                    for member in psutil.Process().children(recursive=True):
                        try:
                            if str(area / version / "agent_toolkit/agents_server_mcp.py") in member.cmdline():
                                owned[member.pid] = member
                                direct_servers.append(member)
                        except psutil.NoSuchProcess:
                            continue
                    assert direct_servers
                    server = direct_servers[-1]
                    arguments = server.cmdline()
                    assert pathlib.Path(arguments[1]).parts[-2:] == ("agent_toolkit", "agents_server_mcp.py")
                    assert pathlib.Path(arguments[1]).is_relative_to(area)
                    assert server.username() == psutil.Process().username()
                    try:
                        opened = [entry._asdict() for entry in server.open_files()]
                        open_files_error = None
                    except psutil.Error as error:
                        opened = []
                        open_files_error = type(error).__name__
                    # 実MCPを1台に限定したOS観測でも共通判定へ届くことを確認する。
                    # インベントリーの対象だけを限定し、PID/argv/username/open_filesは実OSから得る。
                    with monkeypatch.context() as probe:
                        probe.setattr(psutil, "process_iter", lambda: [server])
                        probe.setattr(state_paths, "state_dir", lambda: leases.parent)
                        with plugin_roots.plugin_root_leases.plugin_root_lock(area):
                            assert plugin_roots.plugin_root_leases.plugin_root_in_use(area)
                    protected = cleanup(area)
                    assert protected.returncode == 2, protected.stderr
                    assert area.exists()
                    assert "使用中または使用状態を確認できない配布物のため回収しない" in protected.stderr
                    assert {tool.name for tool in (await client.list_tools()).tools} == tools
                    observation = {
                        "fixture_origin": "90cad03:plugin_root.pyの版別root正常系保持生成",
                        "runtime": "現行MCPと現行launcher。旧版全体の再実行には当たらない",
                        "platform": sys.platform,
                        "launch_processes": process_diagnostics,
                        "pid": server.pid,
                        "created_at": server.create_time(),
                        "cmdline": arguments,
                        "open_files": opened,
                        "open_files_error": open_files_error,
                        "single_real_process_probe": True,
                        "source": str(source),
                        "area": str(area),
                        "live_cleanup": {
                            "exit_code": protected.returncode,
                            "stdout": protected.stdout,
                            "stderr": protected.stderr,
                        },
                    }
            _wait_mcp_processes(owned, timeout=5)
            assert area is not None
            parallel_process = psutil.Process(parallel.pid)
            cleaned = cleanup(area, {parallel.pid: parallel_process})
            assert cleaned.returncode == 0, cleaned.stderr
            assert not area.exists()
            assert parallel.returncode is None
            observation.update(
                terminated_processes=sorted(owned),
                stopped_cleanup={"exit_code": cleaned.returncode, "stdout": cleaned.stdout, "stderr": cleaned.stderr},
                parallel_process={"pid": parallel.pid, "created_at": parallel_process.create_time(), "alive": True},
                root_removed=True,
            )
            print("旧形式MCP観測: " + json.dumps(observation, ensure_ascii=False))
        finally:
            try:
                _wait_mcp_processes(owned, timeout=5)
            finally:
                print("旧形式MCP診断:\\n" + log.read_text(encoding="utf-8"))
