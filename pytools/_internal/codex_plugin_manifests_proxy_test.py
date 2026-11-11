# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""codex_plugin_manifestsのproxyの試験。"""

# pylint: disable=protected-access

import asyncio
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._atk import config as toolkit_config
from agent_toolkit._common import mcp_environment, proxy_environment
from agent_toolkit._testing import isolation

from pytools._internal import codex_plugin_manifests as subject
from pytools._internal import common
from pytools._internal.codex_plugin_manifests_test_helpers import _codex_hooks, _ignore_proxy_notification, _ProxyCodexClient
from pytools._internal.codex_plugin_manifests_test_helpers import _proxy_endpoint as _proxy_endpoint
from pytools._internal.codex_plugin_manifests_test_helpers import _quality_root as _quality_root
from pytools._internal.codex_plugin_manifests_test_helpers import manifest_root_fixture as manifest_root_fixture


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLIが存在しない")
async def test_codex_0154_registers_all_hooks_independent_of_project_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex 0.154.0は`agent-toolkit-codex/`から9イベントを登録し、project trustで集合を変えない。"""
    # 実機のCLIを起動するため、テストの共通設定が隔離するホームとPATHを戻す。
    # CLIの有無はモジュール読込時にホストのPATHで判定済み。
    isolation.restore_host_environment(monkeypatch)
    version = subprocess.run(  # noqa: S603
        ["codex", "--version"], capture_output=True, check=True, text=True
    ).stdout.strip()
    if version != "codex-cli 0.154.0":
        pytest.skip(f"Codex 0.154.0専用のテスト: {version}")

    expected = {event[0].lower() + event[1:] for event in (*subject.CODEX_HOOK_ALLOWLIST, *subject.CODEX_ONLY_HOOKS)}
    observed: list[set[str]] = []
    for trust in (False, True):
        codex_home = tmp_path / ("trusted" if trust else "default")
        codex_home.mkdir()
        if trust:
            quoted_root = str(subject.repo_root()).replace("\\", "\\\\").replace('"', '\\"')
            (codex_home / "config.toml").write_text(f'[projects."{quoted_root}"]\ntrust_level = "trusted"\n', encoding="utf-8")
        environment = {**os.environ, "CODEX_HOME": str(codex_home)}
        subprocess.run(  # noqa: S603
            ["codex", "plugin", "marketplace", "add", str(subject.repo_root())],
            capture_output=True,
            check=True,
            env=environment,
            text=True,
        )
        subprocess.run(  # noqa: S603
            ["codex", "plugin", "add", "agent-toolkit@ak110-dotfiles"],
            capture_output=True,
            check=True,
            env=environment,
            text=True,
        )
        result = await _codex_hooks(codex_home, subject.repo_root(), monkeypatch)
        data = result.get("data")
        assert isinstance(data, list) and len(data) == 1
        hooks = data[0].get("hooks")
        assert isinstance(hooks, list)
        assert data[0].get("warnings") == []
        assert data[0].get("errors") == []
        observed.append({hook["eventName"] for hook in hooks})
        if not trust:
            hook_environment = {
                **environment,
                "TMPDIR": str(tmp_path / "hook-temp"),
                "XDG_STATE_HOME": str(tmp_path / "hook-state"),
            }
            Path(hook_environment["TMPDIR"]).mkdir()
            session_start = next(hook for hook in hooks if hook["eventName"] == "sessionStart")
            start_command = shlex.split(session_start["command"].removesuffix(subject.HOOK_FAILURE_SUFFIX))
            uv = common.resolve_uv_path()
            assert uv is not None
            start_command[0] = str(uv)
            start_result = subprocess.run(  # noqa: S603
                start_command,
                input=json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "integration-session"}),
                capture_output=True,
                check=False,
                env=hook_environment,
                text=True,
            )
            assert start_result.returncode == 0, start_result.stderr
            additional_context = json.loads(start_result.stdout)["hookSpecificOutput"]["additionalContext"]
            state_files = list(Path(hook_environment["XDG_STATE_HOME"]).rglob("*.json"))
            assert len(state_files) == 1
            managed_path = Path(json.loads(state_files[0].read_text(encoding="utf-8"))["path"])
            assert managed_path.is_dir()
            assert str(managed_path) in additional_context

            session_end = next(hook for hook in hooks if hook["eventName"] == "sessionEnd")
            end_command = shlex.split(session_end["command"].removesuffix(subject.HOOK_FAILURE_SUFFIX))
            end_command[0] = str(uv)
            subprocess.run(  # noqa: S603
                end_command,
                input=json.dumps({"hook_event_name": "SessionEnd", "session_id": "integration-session", "reason": "other"}),
                capture_output=True,
                check=True,
                env=hook_environment,
                text=True,
            )
            assert not managed_path.exists()

    assert observed == [expected, expected]


@pytest.mark.asyncio
async def test_proxy_contract_is_shared_with_codex_backend(manifest_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """静的な共通名の追加と動的な親環境の配送を分け、環境によらず共通の生成物を保つ。"""
    names = (*mcp_environment.PROXY_ENVIRONMENT_VARIABLES, "TEST_PROXY")
    monkeypatch.setattr(mcp_environment, "PROXY_ENVIRONMENT_VARIABLES", names)
    monkeypatch.setitem(toolkit_config._MUTABLE_KEY_DEFAULTS, "future_engine", "codex")  # pylint: disable=protected-access
    expected_names = list(mcp_environment.environment_variable_names(toolkit_config.config_environment_variable_names()))
    assert "AGENT_TOOLKIT_CONFIG_FUTURE_ENGINE" in expected_names
    source_before = (manifest_root / subject.MCP_SOURCE).read_bytes()
    subject.sync(manifest_root)
    codex = json.loads((manifest_root / subject.MCP_CODEX_TARGET).read_text(encoding="utf-8"))
    portable = json.loads((manifest_root / subject.AGENT_MCP_TARGET).read_text(encoding="utf-8"))
    for name in ("agents_server", "pyfltr"):
        assert codex["mcpServers"][name]["env_vars"] == expected_names
        assert "env_vars" not in portable["mcpServers"][name]
    before = {path.relative_to(manifest_root): path.read_bytes() for path in manifest_root.rglob("*") if path.is_file()}
    monkeypatch.setenv("TEST_PROXY", "synthetic-proxy")
    monkeypatch.setenv("UNKNOWN_VENDOR_API_KEY", "synthetic-credential")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-token")
    async with codex_backend.AppServerManager() as manager:
        backend = manager._agents_server_config("owner", "writer")  # pylint: disable=protected-access
    runtime_names = backend["mcp_servers"]["agents_server"]["env_vars"]
    assert {"TEST_PROXY", "UNKNOWN_VENDOR_API_KEY", "ANTHROPIC_AUTH_TOKEN"} <= set(runtime_names)
    assert "UNKNOWN_VENDOR_API_KEY" not in expected_names
    subject.sync(manifest_root)
    after = {path.relative_to(manifest_root): path.read_bytes() for path in manifest_root.rglob("*") if path.is_file()}
    assert after == before
    assert (manifest_root / subject.MCP_SOURCE).read_bytes() == source_before


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLIが存在しない")
@pytest.mark.parametrize("index", [0, 1, 2], ids=["plugin-agents-server", "plugin-pyfltr", "backend-agents-server"])
async def test_codex_stdio_inherits_proxy_environment(
    manifest_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, proxy_endpoint: tuple[str, list[str]], index: int
) -> None:
    """実Codexの起動・再開で保存先と公開overrideを保持し、専用proxyへ要求が届く。

    実Codexの起動は1件あたり2回かかるため、設定ごとに試験を分けて60秒の上限を各設定が個別に持つ。
    """
    isolation.restore_host_environment(monkeypatch)
    proxy_url, requests = proxy_endpoint
    inherited = {
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "HTTP_PROXY": proxy_url,
        "no_proxy": "localhost,127.0.0.1",
        "GLOBAL_AGENT_HTTP_PROXY": proxy_url,
    }
    for name in proxy_environment.PROXY_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    namespace = {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "AGENT_TOOLKIT_PRIVATE_NOTES": str(tmp_path / "notes"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "CODEX_HOME": str(codex_home),
        "AGENT_TOOLKIT_CONFIG_BASE_ENGINE": "codex",
        "ANTHROPIC_AUTH_TOKEN": "synthetic-auth-token",
        "ANTHROPIC_AWS_BASE_URL": "https://synthetic.invalid",
        "ANTHROPIC_AWS_WORKSPACE_ID": "synthetic-workspace",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS": "1",
        "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH": "1",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "synthetic-opus",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "synthetic-sonnet",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "synthetic-haiku",
        "OPENAI_API_KEY": "synthetic-openai-key",
        "GEMINI_API_KEY": "synthetic-gemini-key",
        "DBUS_SESSION_BUS_ADDRESS": "synthetic-keyring-address",
        "LC_ALL": "C",
    }
    for name, value in namespace.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("UNKNOWN_VENDOR_API_KEY", "synthetic-unknown-key")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    monkeypatch.delenv("UNSET_VENDOR_SETTING", raising=False)
    observed_names = [*namespace, "UNKNOWN_VENDOR_API_KEY", "UNSET_VENDOR_SETTING", "CLAUDE_CODE_SESSION_ID"]
    monkeypatch.setattr(codex_backend, "APP_SERVER_WORKING_DIRECTORY", str(tmp_path))
    child = tmp_path / "proxy-child.py"
    child.write_text(
        "import json, os, urllib.request\n"
        "from pathlib import Path\n"
        "response = urllib.request.urlopen('http://proxy-probe.invalid/resource', timeout=5).read().decode()\n"
        "Path(os.environ['OBS_PATH']).write_text(json.dumps({'env': {name: os.environ[name] for name in "
        f"{list(proxy_environment.PROXY_ENVIRONMENT_VARIABLES)!r} if name in os.environ}}, "
        f"'namespace': {{name: os.environ.get(name) for name in {observed_names!r}}}, "
        "'response': response}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "proxy-mcp.py"
    launcher.write_text(
        "import os, subprocess, sys\nfrom pathlib import Path\n"
        "sys.stderr = open(os.environ['OBS_PATH'] + '.stderr', 'w', encoding='utf-8')\n"
        "from mcp.server.mcpserver import MCPServer\n"
        f"subprocess.run([sys.executable, {str(child)!r}], check=True, timeout=10)\n"
        "server = MCPServer('proxy-probe')\n"
        "@server.tool()\ndef observed() -> str:\n    return 'ready'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    subject.sync(manifest_root)
    plugin_servers = json.loads((manifest_root / subject.MCP_CODEX_TARGET).read_text(encoding="utf-8"))["mcpServers"]
    async with codex_backend.AppServerManager() as manager:
        backend = manager._agents_server_config("owner", "writer")["mcp_servers"]["agents_server"]  # pylint: disable=protected-access
    configurations = [plugin_servers["agents_server"], plugin_servers["pyfltr"], backend]
    processes: list[asyncio.subprocess.Process] = []
    async with contextlib.AsyncExitStack() as resources:
        client = await resources.enter_async_context(_ProxyCodexClient(_ignore_proxy_notification, _ignore_proxy_notification))
        async with asyncio.timeout(60):
            await client.start()
            assert client.process is not None
            processes.append(client.process)
            configuration = configurations[index]
            expected_namespace = {
                **namespace,
                "UNKNOWN_VENDOR_API_KEY": "synthetic-unknown-key" if index == 2 else None,
                "UNSET_VENDOR_SETTING": None,
                "CLAUDE_CODE_SESSION_ID": None,
            }
            output = tmp_path / f"child-{index}.json"
            server = {
                **configuration,
                "command": sys.executable,
                "args": [str(launcher)],
                "cwd": str(tmp_path),
                "env": {**configuration.get("env", {}), "OBS_PATH": str(output)},
            }
            server.pop("type", None)
            response = await client.request(
                "thread/start",
                {
                    "cwd": str(tmp_path),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "config": {"mcp_servers": {"probe": server}, "features": {"plugins": False, "apps": False}},
                },
            )
            thread_id = response["thread"]["id"]
            inventory = await client.request("mcpServerStatus/list", {"threadId": thread_id, "serverName": "probe"})
            assert inventory["data"], inventory
            diagnostic = Path(str(output) + ".stderr")
            assert inventory["data"][0]["runtimeStatus"] == "connected", json.dumps(inventory) + (
                diagnostic.read_text(encoding="utf-8") if diagnostic.exists() else ""
            )
            observation = json.loads(output.read_text(encoding="utf-8"))
            assert observation == {"env": inherited, "namespace": expected_namespace, "response": "proxy-observed"}
            # モデルを呼ばず履歴を保存する0.162.1の公開実験APIで、cold resumeの入力を準備する。
            await client.request(
                "thread/inject_items",
                {
                    "threadId": thread_id,
                    "items": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fixture"}]}],
                },
            )
            await client.close()
            output.unlink()
            client = await resources.enter_async_context(
                _ProxyCodexClient(_ignore_proxy_notification, _ignore_proxy_notification)
            )
            await client.start()
            assert client.process is not None
            processes.append(client.process)
            await client.request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "cwd": str(tmp_path),
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                    "config": {"mcp_servers": {"probe": server}},
                },
            )
            inventory = await client.request("mcpServerStatus/list", {"threadId": thread_id, "serverName": "probe"})
            assert inventory["data"][0]["runtimeStatus"] == "connected", json.dumps(inventory)
            assert json.loads(output.read_text(encoding="utf-8")) == {
                "env": inherited,
                "namespace": expected_namespace,
                "response": "proxy-observed",
            }
            await client.request("thread/unsubscribe", {"threadId": thread_id})
    assert processes and all(process.returncode is not None for process in processes)
    assert requests.count("http://proxy-probe.invalid/resource") == 2


__all__ = ["manifest_root_fixture"]
