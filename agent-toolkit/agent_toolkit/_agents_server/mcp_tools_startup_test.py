"""mcp_toolsのstartupの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import logging
import os
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit._agents_server import child_environment, launch_requests, logging_config, mcp_tools, service
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._common import mcp_environment, session_registry, state_paths

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_main_strips_conversation_context_before_backend_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", None, raising=False)
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    for name in mcp_environment._NON_INHERITABLE_NAMES:
        monkeypatch.setenv(name.upper(), "parent-session")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    observed: dict[str, str] = {}

    def check_dependencies() -> None:
        observed.update(os.environ)

    monkeypatch.setattr(claude_backend, "check_dependencies", check_dependencies)
    assert entry_script.main(["--check-dependencies"]) == 0
    assert not {name.casefold() for name in observed} & mcp_environment._NON_INHERITABLE_NAMES
    assert "ANTHROPIC_AUTH_TOKEN" not in observed
    inherited = child_environment.inherited(observed)
    assert inherited["ANTHROPIC_AUTH_TOKEN"] == "test-token"
    assert not {name.casefold() for name in inherited} & mcp_environment._NON_INHERITABLE_NAMES
    assert launch_requests._HANDOFF_SESSION == ("parent-session",)


def test_main_strips_launcher_venv_from_child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動処理が後続工程より前に、起動元ツールの仮想環境を`VIRTUAL_ENV`と`PATH`から取り除く。"""
    venv_root = "/tmp/agents-server-environment"
    monkeypatch.setenv("VIRTUAL_ENV", venv_root)
    monkeypatch.setenv("PATH", os.pathsep.join([f"{venv_root}/bin", "/usr/local/bin", "", "/usr/bin"]))
    observed: dict[str, str | None] = {}

    def check_dependencies() -> None:
        observed["virtual_env"] = os.environ.get("VIRTUAL_ENV")
        observed["path"] = os.environ.get("PATH")

    monkeypatch.setattr(claude_backend, "check_dependencies", check_dependencies)
    assert entry_script.main(["--check-dependencies"]) == 0
    assert observed["virtual_env"] is None
    assert observed["path"] == os.pathsep.join(["/usr/local/bin", "", "/usr/bin"])


def test_dependency_check_cli_does_not_start_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(claude_backend, "check_dependencies", lambda: calls.append(True))
    monkeypatch.setattr(mcp_tools.mcp, "run", lambda **_kwargs: pytest.fail("MCPを起動してはいけない"))

    assert entry_script.main(["--check-dependencies"]) == 0
    assert calls == [True]


def test_dependency_check_cli_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:

    def fail_check() -> None:
        raise ImportError("claude-agent-sdk is unavailable")

    monkeypatch.setattr(claude_backend, "check_dependencies", fail_check)
    with pytest.raises(ImportError, match="claude-agent-sdk is unavailable"):
        entry_script.main(["--check-dependencies"])


def test_main_persists_startup_and_exit_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(claude_backend, "check_dependencies", lambda: None)

    assert entry_script.main(["--check-dependencies"]) == 0

    log_path = tmp_path / "agents-server.log"
    content = log_path.read_text(encoding="utf-8")
    assert "agents_serverを起動します: mode=check-dependencies" in content
    assert "agents_serverが正常終了しました: mode=check-dependencies" in content
    handlers = logging.getLogger("agent-toolkit.agents-server").handlers
    assert not any(isinstance(handler, logging_config._LogFileHandler) for handler in handlers)  # pylint: disable=protected-access  # noqa: SLF001


@pytest.mark.asyncio
async def test_mcp_lifespan_persists_activate_and_close_milestones(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    mcp_tools._configure_logging()
    monkeypatch.setattr(mcp_tools._MANAGER, "activate_async", AsyncMock())
    monkeypatch.setattr(mcp_tools._MANAGER, "close", AsyncMock())

    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        pass

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "manager activateを開始します" in content
    assert "manager activateが完了しました" in content
    assert "manager closeを開始します" in content
    assert "manager closeが完了しました" in content


@pytest.mark.asyncio
async def test_mcp_lifespan_persists_activate_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """manager activateの開始と失敗を例外診断とともに永続化する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    mcp_tools._configure_logging()

    async def fail_activate() -> None:
        raise RuntimeError("activate failed")

    monkeypatch.setattr(mcp_tools._MANAGER, "activate_async", fail_activate)

    with pytest.raises(RuntimeError, match="activate failed"):
        async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
            pytest.fail("activate失敗後にlifespanへ入ってはいけない")

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "manager activateを開始します" in content
    assert "manager activateに失敗しました: stage=manager_activate" in content
    assert "RuntimeError: activate failed" in content


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_message", [False, True])
async def test_service_records_exception_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, secret_message: bool
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", None)
    monkeypatch.delenv(logging_config.LAUNCH_RECORD_ENV, raising=False)
    message = "token=" + "fake-long-value" * 300 + " stage=start" if secret_message else "transport broke"

    class FailingManagers:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> None:
            raise RuntimeError(message)

        async def __aexit__(self, *_exc: object) -> None:
            pass

    monkeypatch.setattr(service, "ServiceManagers", FailingManagers)
    state_root = tmp_path / "service-state"
    with pytest.raises(RuntimeError):
        await service.run(state_root / "services" / "agents-server", "G1")

    [path] = logging_config.instance_directory(state_root).glob("*.jsonl")
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["started", "exited"]
    assert events[1] | {"at": None} == {
        "at": None,
        "event": "exited",
        "writer": "server",
        "outcome": "exception",
        "exception_type": "RuntimeError",
        "message": 'token="[REDACTED]" stage=start' if secret_message else "transport broke",
    }

    assert logging_config.current_server_instance() is None
    assert session_registry.owner_server_instance() is None
