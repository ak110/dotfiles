"""委譲先の子プロセスへ、起動層が設定した共有依存環境を渡さない契約を検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import sys
from collections.abc import Generator
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import (
    child_environment,
    claude,
    cli_turn,
    codex,
    launch_requests,
    launch_requests_preflight,
    mcp_tools,
)
from agent_toolkit._atk import claude_connection
from agent_toolkit._common import inherited_venv, plugin_environment, runtime_environment
from agent_toolkit._testing.agents_server_support import AssistantMessage, QueueClaudeClient

_ROOT = pathlib.Path(child_environment.__file__).resolve().parents[2]
_OBSERVED = ("UV_PROJECT_ENVIRONMENT", inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY, inherited_venv.LAUNCH_RESTORE_KEY)


async def _ignore_message(_value: dict[str, Any]) -> None:
    pass


@contextlib.contextmanager
def _launched(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, *, bound: bool, user: str | None) -> Generator[str]:
    caller = {key: value for key, value in os.environ.items() if key != "UV_PROJECT_ENVIRONMENT"}
    if user is not None:
        caller["UV_PROJECT_ENVIRONMENT"] = user
    launched = plugin_environment.launch_environment(_ROOT, caller)
    if bound:
        with runtime_environment.bind(runtime_environment.CallerContext(launched, tmp_path)):
            yield launched["UV_PROJECT_ENVIRONMENT"]
        return
    for key in _OBSERVED:
        monkeypatch.delenv(key, raising=False)
    for key, value in launched.items():
        monkeypatch.setenv(key, value)
    yield launched["UV_PROJECT_ENVIRONMENT"]


@pytest.mark.parametrize("bound", [True, False], ids=["caller-context", "process-environment"])
@pytest.mark.parametrize("user", [None, "/caller/project-env"], ids=["user-unset", "user-set"])
def test_preflight_child_receives_caller_project_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, bound: bool, user: str | None
) -> None:
    output = tmp_path / "child-env.json"
    command = (
        sys.executable,
        "-c",
        "import json, os, sys; open(sys.argv[1], 'w').write(json.dumps({k: os.environ.get(k) for k in sys.argv[2:]}))",
        str(output),
        *_OBSERVED,
    )
    with _launched(monkeypatch, tmp_path, bound=bound, user=user) as shared:
        assert launch_requests_preflight._run_preflight_command(command, str(tmp_path)) is None  # pylint: disable=protected-access

    observed = json.loads(output.read_text(encoding="utf-8"))
    assert observed == {"UV_PROJECT_ENVIRONMENT": user, **dict.fromkeys(_OBSERVED[1:])}
    assert shared not in observed.values()


@pytest.mark.parametrize("bound", [True, False], ids=["caller-context", "process-environment"])
@pytest.mark.parametrize("user", [None, "/caller/project-env"], ids=["user-unset", "user-set"])
@pytest.mark.asyncio
async def test_codex_app_server_receives_caller_project_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, bound: bool, user: str | None
) -> None:
    """Codex App Serverの起動環境も同じ契約で共有依存環境を外す。"""
    captured: dict[str, str] = {}

    async def spawn(*_args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs["env"])
        raise OSError("起動境界で停止")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with _launched(monkeypatch, tmp_path, bound=bound, user=user):
        client = codex.JsonRpcProcess(_ignore_message, _ignore_message)
        with pytest.raises(codex.AppServerError, match="failed to start"):
            await client.start()

    assert {key: captured.get(key) for key in _OBSERVED} == {"UV_PROJECT_ENVIRONMENT": user, **dict.fromkeys(_OBSERVED[1:])}


@pytest.mark.parametrize("bound", [True, False], ids=["caller-context", "process-environment"])
@pytest.mark.parametrize("user", [None, "/caller/project-env"], ids=["user-unset", "user-set"])
def test_cli_turn_child_receives_caller_project_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, bound: bool, user: str | None
) -> None:
    """1ターンずつ起動するCLIの委譲先も同じ契約で共有依存環境を外す。"""
    with _launched(monkeypatch, tmp_path, bound=bound, user=user):
        env = cli_turn._child_env(None)  # pylint: disable=protected-access

    assert {key: env.get(key) for key in _OBSERVED} == {"UV_PROJECT_ENVIRONMENT": user, **dict.fromkeys(_OBSERVED[1:])}


@pytest.mark.parametrize("bound", [True, False], ids=["caller-context", "process-environment"])
@pytest.mark.parametrize("user", [None, "/caller/project-env"], ids=["user-unset", "user-set"])
@pytest.mark.asyncio
async def test_claude_sdk_child_receives_caller_project_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, bound: bool, user: str | None
) -> None:
    """Claude SDKの子は継承環境とSDK環境の合成を受け取るため、管理サービスの起動時整備と要求ごとの分離の両方で外す。"""
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", None)
    selected = claude_connection.Connection("inherit")
    monkeypatch.setattr(claude_connection, "candidates", lambda *_args, **_kwargs: claude_connection.Candidates((selected,)))
    effective: list[dict[str, str]] = []

    def factory(options: Any) -> QueueClaudeClient:
        text = options.settings if options.settings.startswith("{") else pathlib.Path(options.settings).read_text()
        effective.append({**os.environ, **options.env, **json.loads(text).get("env", {})})
        return QueueClaudeClient(
            [[claude_agent_sdk.SystemMessage("init", {"session_id": "sdk-env"}), AssistantMessage("開始")]]
        )

    with _launched(monkeypatch, tmp_path, bound=bound, user=user):
        if not bound:
            mcp_tools._prepare_child_environment()  # pylint: disable=protected-access
        async with claude.ClaudeServerManager(client_factory=factory) as backend:
            await backend.start("調査", str(tmp_path))

    assert {key: effective[0].get(key) for key in _OBSERVED} == {
        "UV_PROJECT_ENVIRONMENT": user,
        **dict.fromkeys(_OBSERVED[1:]),
    }
