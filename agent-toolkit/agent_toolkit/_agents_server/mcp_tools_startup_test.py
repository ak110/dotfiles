"""mcp_toolsのstartupの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
from multiprocessing import resource_tracker
from typing import Any
from unittest.mock import AsyncMock

import psutil
import pytest

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit._agents_server import child_environment, launch_requests, lifetime, logging_config, mcp_tools
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._common import mcp_environment, session_registry, state_paths

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_main_strips_conversation_context_before_backend_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP起動は全backendの継承元を整え、接続設定と引き継ぎ元snapshotを保持する。"""
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


def test_backend_imports_survive_plugin_path_removal(tmp_path: pathlib.Path) -> None:
    """MCP初期化後にプラグイン配置を除去しても両backendを生成できる。"""
    source_dir = pathlib.Path(__file__).resolve().parents[1]
    script_dir = tmp_path / "plugin" / "scripts"
    script_dir.mkdir(parents=True)
    rules_dir = tmp_path / "plugin" / "share" / "rules"
    rules_dir.mkdir(parents=True)
    shutil.copyfile(source_dir.parent / "share" / "rules" / "rules-subagent.md", rules_dir / "rules-subagent.md")
    paths = (
        "agents_server_mcp.py",
        "_agents_server/mcp_tools.py",
        "_agents_server/mcp_transport.py",
        "_agents_server/manager.py",
        "_agents_server/launch_requests.py",
        "_agents_server/responses.py",
        "_agents_server/tool_descriptions.py",
        "_agents_server/engine_availability.py",
        "_agents_server/codex.py",
        "_agents_server/codex_clients.py",
        "_agents_server/codex_connections.py",
        "_agents_server/codex_instructions.py",
        "_agents_server/codex_notifications.py",
        "_agents_server/codex_protocol.py",
        "_agents_server/codex_replies.py",
        "_agents_server/codex_reset.py",
        "_agents_server/codex_rpc.py",
        "_agents_server/codex_start.py",
        "_agents_server/codex_turns.py",
        "_agents_server/claude.py",
        "_agents_server/antigravity.py",
        "_common/process_tree.py",
        "_agents_server/state.py",
        "_agents_server/status_file.py",
        "_agents_server/file_operations.py",
        "_common/session_registry.py",
        "_common/atomic_file.py",
        "_atk/config.py",
        "_atk/help_text.py",
        "_common/inherited_venv.py",
        "_common/delegated_session.py",
        "_plan/locations.py",
        "_common/wait_schedule.py",
    )
    for relative_path in paths:
        destination = script_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / relative_path, destination)
    for package in ("_agents_server", "_common", "_atk", "_plan"):
        (script_dir / package / "__init__.py").write_text("", encoding="utf-8")

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import shutil, sys\n"
                "from pathlib import Path\n"
                "script_dir = Path(sys.argv[1])\n"
                "sys.path.insert(0, str(script_dir))\n"
                "import agents_server_mcp as entry_script\n"
                "assert 'claude_agent_sdk' not in sys.modules\n"
                "sys.path.remove(str(script_dir))\n"
                "shutil.rmtree(script_dir)\n"
                "assert entry_script.mcp_tools._MANAGER._backend('codex').__class__.__name__ == 'AppServerManager'\n"
                "assert entry_script.mcp_tools._MANAGER._backend('claude').__class__.__name__ == 'ClaudeServerManager'\n"
                "assert entry_script.mcp_tools._MANAGER._backend('agy').__class__.__name__ == 'AntigravityManager'\n"
            ),
            str(script_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


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
    """依存の確認を指定した場合はMCP stdioを起動しない。"""
    calls: list[bool] = []
    monkeypatch.setattr(claude_backend, "check_dependencies", lambda: calls.append(True))
    monkeypatch.setattr(mcp_tools.mcp, "run", lambda **_kwargs: pytest.fail("MCPを起動してはいけない"))

    assert entry_script.main(["--check-dependencies"]) == 0
    assert calls == [True]


def test_dependency_check_cli_propagates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """依存の確認処理で発生した例外を捕捉せず呼び出し元へ伝える。"""

    def fail_check() -> None:
        raise ImportError("claude-agent-sdk is unavailable")

    monkeypatch.setattr(claude_backend, "check_dependencies", fail_check)
    with pytest.raises(ImportError, match="claude-agent-sdk is unavailable"):
        entry_script.main(["--check-dependencies"])


def test_main_persists_startup_and_exit_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """依存の確認を含む起動初期の診断を状態ディレクトリへ永続化する。"""
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
    """managerのactivate前後とclose前後を永続化する。"""
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


@pytest.mark.parametrize("stop_requested", [False, True])
@pytest.mark.parametrize("secret_message", [False, True])
def test_serve_records_exception_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, stop_requested: bool, secret_message: bool
) -> None:
    """stdio起動が例外で終わると、起動単位の終了診断へ例外型とメッセージを記録し、例外を呼出元へ伝える。

    `serve`はこの試験ではpytestのプロセスの中で動く。終了時の回収は自プロセスの全子孫を対象にするため、
    そのまま動かすと、pytestのインタープリターが終了まで使うresource_trackerと常駐のfork-serverまで止める。
    止まったtrackerは、同じworkerで次にspawnの資源を使う試験で`process died unexpectedly, relaunching`の
    警告とともに起動し直される。回収そのものは`lifetime_test.py`が別プロセスの`serve`で確かめる。
    """
    monkeypatch.setattr(lifetime.ServerLifetime, "_reclaim", lambda _self: [])
    resource_tracker.ensure_running()
    # 起動PIDは公開APIを持たないため、標準ライブラリの内部属性から読む。
    tracker = getattr(resource_tracker._resource_tracker, "_pid", None)
    assert tracker is not None
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", None)
    for name in ("CLAUDE_CODE_SESSION_ID", logging_config.LAUNCH_RECORD_ENV):
        monkeypatch.delenv(name, raising=False)

    message = "token=" + "fake-long-value" * 300 + " stage=start" if secret_message else "transport broke"

    def fail(**_kwargs: Any) -> None:
        if stop_requested:
            assert mcp_tools.mcp.server_lifetime is not None
            mcp_tools.mcp.server_lifetime.request_stop()
        raise RuntimeError(message)

    monkeypatch.setattr(mcp_tools.mcp, "run", fail)
    with pytest.raises(RuntimeError):
        mcp_tools.serve()

    [path] = logging_config.instance_directory(tmp_path).glob("*.jsonl")
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
    # 終えた起動単位は同じプロセスの後続の記録へ書かない。
    assert logging_config.current_server_instance() is None
    assert session_registry.owner_server_instance() is None
    # 試験のプロセスが使うtrackerを止めていない。止めていれば次の利用でPIDが変わる。
    assert psutil.Process(tracker).status() != psutil.STATUS_ZOMBIE
    resource_tracker.ensure_running()
    assert getattr(resource_tracker._resource_tracker, "_pid", None) == tracker
