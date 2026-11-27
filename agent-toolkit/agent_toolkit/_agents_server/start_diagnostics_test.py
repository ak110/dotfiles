import asyncio
import datetime
import json
import os
import pathlib
import subprocess
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from agent_toolkit._agents_server import (
    antigravity,
    codex,
    launch_requests,
    manager,
    mcp_tools,
    unavailable_candidates,
)
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError
from agent_toolkit._testing import agents_server_support

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def _agy_environment(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, cause: str) -> list[list[str]]:
    home = tmp_path / "agy-home"
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    body = {} if cause == "opt-in" else {"modelProvider": "gemini"}
    settings.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("USERPROFILE" if os.name == "nt" else "HOME", str(home))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    if cause != "key":
        monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key-not-for-model-use")
    monkeypatch.setattr(antigravity.shutil, "which", lambda *_a, **_k: None if cause == "missing-cli" else "agy")
    probes: list[list[str]] = []

    def version_probe(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        probes.append(command)
        assert "GEMINI_API_KEY" not in kwargs["env"]
        if cause == "probe-error":
            raise OSError("version probe failed")
        return subprocess.CompletedProcess(command, 0, "1.5.12" if cause == "old-cli" else "1.5.13")

    monkeypatch.setattr(antigravity.process_tree, "run_with_timeout", version_probe)

    async def no_model_process(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("前提不成立なのにモデルCLIを起動した")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_model_process)
    return probes


@pytest.mark.parametrize(
    ("cause", "operation", "probe_count"),
    [
        ("opt-in", '"modelProvider": "gemini"', 0),
        ("key", "起動元へGEMINI_API_KEYを設定", 0),
        ("missing-cli", "導入", 0),
        ("old-cli", "1.5.13以降へ更新", 1),
        ("probe-error", "agy --version", 1),
    ],
)
@pytest.mark.asyncio
async def test_public_start_agy_preconditions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, cause: str, operation: str, probe_count: int
) -> None:
    probes = _agy_environment(tmp_path, monkeypatch, cause)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", "agy:gemini-test/medium")
    async with manager.AgentsServerManager(None) as current:
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        with pytest.raises(ToolError) as error:
            await mcp_tools.mcp.call_tool("start", {"mode": "write", "cwd": str(tmp_path), "prompt": "起草"})
        reason, action = str(error.value).split(NEXT_ACTION_PREFIX, 1)
        assert "agy" in reason and "gemini-test" in reason
        assert "agy:gemini-test/medium" in action and operation in action
        assert "待" not in action
        assert "synthetic-key-not-for-model-use" not in str(error.value)
        assert len(probes) == probe_count
        assert not current.sessions


class _LimitedClient(agents_server_support.FakeCodexClient):
    def __init__(self, known_reset: bool) -> None:
        super().__init__()
        self.known_reset = known_reset

    async def request(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if method == "account/read":
            self.requests.append((method, params or {}))
            return {"account": {"type": "chatgpt"}}
        if method == "account/rateLimits/read":
            self.requests.append((method, params or {}))
            window = {"usedPercent": 100, "windowDurationMins": 10080}
            if self.known_reset:
                window["resetsAt"] = 2000000000
            return {"ordinaryUsageAllowed": False, "rateLimits": {"primary": window}}
        return await super().request(method, params, **kwargs)


@pytest.mark.parametrize("known_reset", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.asyncio
async def test_public_start_usage_limit_and_mixed_causes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, known_reset: bool, mixed: bool
) -> None:
    _agy_environment(tmp_path, monkeypatch, "opt-in")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_WORKER_CONNECTIONS", "subscription")
    candidates = "codex:gpt-6-sol/medium" + (",agy:gemini-test/medium" if mixed else "")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", candidates)
    client = _LimitedClient(known_reset)
    async with manager.AgentsServerManager(None) as current, codex.AppServerManager(current.sessions) as limited:

        async def ensure_client(*_args: Any, **_kwargs: Any) -> _LimitedClient:
            return client

        monkeypatch.setattr(limited, "_ensure_client", ensure_client)
        agents_server_support.install_backend(current, "codex", limited)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        with pytest.raises(ToolError) as error:
            await mcp_tools.mcp.call_tool("start", {"mode": "write", "cwd": str(tmp_path), "prompt": "起草"})
        reason, action = str(error.value).split(NEXT_ACTION_PREFIX, 1)
        assert "利用上限" in reason
        assert "codex:gpt-6-sol/medium" in action and "待" in action and "同じ工程名で再試行" in action
        assert ("2033-05-18T03:33:20+00:00" in action) is known_reset
        if not known_reset:
            assert "解除時刻は不明" in action
        assert "atk config set" not in action and "API課金" not in action
        if mixed:
            assert "Gemini APIキー方式" in reason
            assert action.index("modelProvider") < action.index("codex:gpt-6-sol/medium")
        assert not current.sessions
        assert [method for method, _ in client.requests] == ["config/read", "account/read", "account/rateLimits/read"]


@pytest.mark.parametrize("recorded", [False, True])
@pytest.mark.asyncio
async def test_public_start_unknown_reason_is_not_a_retry_or_authentication_diagnosis(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, recorded: bool
) -> None:
    candidates = "agy:unknown/medium" + (",agy:recorded/high" if recorded else "")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", candidates)
    if recorded:
        unavailable_candidates.record_unavailable_candidate(
            "write",
            "write",
            ("agy", "recorded", "high"),
            "opaque-saved-reason",
            now=datetime.datetime.now(datetime.UTC),
        )
    async with manager.AgentsServerManager(None) as current:
        backend = agents_server_support.FakeBackend(current.sessions, "agy")
        attempted: list[str | None] = []

        async def fail(_prompt: str, _cwd: str, model: str | None = None, _effort: str | None = None, **_kwargs: Any) -> Any:
            attempted.append(model)
            raise RuntimeError("opaque-CLI-error")

        monkeypatch.setattr(backend, "start", fail)
        agents_server_support.install_backend(current, "agy", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        with pytest.raises(ToolError) as error:
            await mcp_tools.mcp.call_tool("start", {"mode": "write", "cwd": str(tmp_path), "prompt": "起草"})
        reason, action = str(error.value).split(NEXT_ACTION_PREFIX, 1)
        assert "opaque-CLI-error" in reason and "起動診断を確認" in action
        if recorded:
            assert "opaque-saved-reason" in reason and "agy:recorded/high" in action
        assert attempted == ["unknown"]
        assert not any(word in action for word in ("待", "認証", "設定変更"))
        assert not current.sessions


@pytest.mark.parametrize("denied", [False, True])
@pytest.mark.asyncio
async def test_public_start_preserves_successful_fallback_and_prelaunch_denial(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, denied: bool
) -> None:
    _agy_environment(tmp_path, monkeypatch, "key")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", "agy:gemini-test/medium,claude:test/medium")
    async with manager.AgentsServerManager(None) as current:
        selected = agents_server_support.OutputObservedBackend(current.sessions, "claude", current._condition)  # pylint: disable=protected-access
        agents_server_support.install_backend(current, "claude", selected)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        if denied:

            async def reject(_cwd: str, **_kwargs: Any) -> None:
                raise ActionableError("access-denied", next_action="必要な認可を得る")

            monkeypatch.setattr(launch_requests, "check_plugin_commands", reject)
            with pytest.raises(ToolError, match="access-denied") as error:
                await mcp_tools.mcp.call_tool("start", {"mode": "write", "cwd": str(tmp_path), "prompt": "起草"})
            assert "必要な認可を得る" in str(error.value) and "no available model candidates" not in str(error.value)
            assert not selected.start_calls and not current.sessions
        else:
            result = await mcp_tools.mcp.call_tool("start", {"mode": "write", "cwd": str(tmp_path), "prompt": "起草"})
            assert isinstance(result, CallToolResult)
            assert result.structured_content is not None
            response = result.structured_content
            assert response["engine"] == "claude"
            assert response["excluded_candidates"][0]["engine"] == "agy"
            assert "GEMINI_API_KEY" in response["excluded_candidates"][0]["reason"]
            assert selected.start_calls == [("test", "medium", "write")]
