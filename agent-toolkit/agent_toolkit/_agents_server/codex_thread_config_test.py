"""Codexのthread_configを検証する。"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shlex
import shutil
import sys
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server import shared_roots, state
from agent_toolkit._common import inherited_venv, plugin_environment
from agent_toolkit._testing.agents_server_support import FakeCodexClient, _complete
from agent_toolkit._testing.codex_backend_support import _return, _return_none, _ThreadStartClient, _TierClient
from agent_toolkit._testing.codex_plugin_support import _make_plugin_root, _owned_async_child

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.asyncio
async def test_thread_start_overrides_agents_server_with_owner_and_writer_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """内側MCPサーバーへルートと書込主体を完全な定義で配送する。"""
    client = _ThreadStartClient()
    runtime_data = tmp_path / "plugin data"
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(runtime_data))
    async with subject.AppServerManager(root_session_id="root-session") as manager:
        monkeypatch.setattr(manager, "_ensure_client", lambda *_args, **_kwargs: _return(client))
        monkeypatch.setattr(
            shared_roots, "write_host_alias", lambda root, writer, thread: aliases.append((root, writer, thread))
        )
        aliases: list[tuple[str, str, str]] = []
        monkeypatch.setattr(manager, "_start_turn", lambda *_args: _return_none())

        await manager.start("実装する", str(tmp_path))

        assert client.params is not None
        server = client.params["config"]["mcp_servers"]["agents_server"]
        assert server["command"] == "uv"
        assert server["args"][-1].endswith("agent_toolkit/agents_server_mcp.py")
        assert set(server["env"]) == {
            "AGENT_TOOLKIT_OWNER_SESSION",
            "AGENT_TOOLKIT_STATUS_HOST_SESSION",
            "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT",
            plugin_environment.RUNTIME_DATA_ENV,
        }
        assert server["env"][plugin_environment.RUNTIME_DATA_ENV] == str(runtime_data)
        assert server["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == "root-session"
        assert server["default_tools_approval_mode"] == "approve"
        parent_environment = dict(os.environ)
        inherited_venv.strip_inherited_venv(parent_environment)
        assert server["env_vars"] == list(subject.mcp_environment.runtime_environment_variable_names(parent_environment))
        assert "AGENT_TOOLKIT_DELEGATED_SESSION" not in server["env"]
        assert aliases == [("root-session", server["env"]["AGENT_TOOLKIT_STATUS_HOST_SESSION"], "inner-thread")]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "resume", "reply"])
@pytest.mark.parametrize("configured", [False, True])
async def test_mcp_mise_namespace_names_and_explicit_owner_are_separate(
    operation: str, configured: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """開始・追送・保存会話の再開で親の任意名を配送し、起動contextは明示値を優先する。"""
    runtime_data = tmp_path / "plugin data"
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(runtime_data))
    names = ("MISE_STATE_DIR", "MISE_CACHE_DIR", "MISE_DATA_DIR", "MISE_CONFIG_DIR")
    values = {name: str(tmp_path / name.lower()) for name in names}
    for name, value in values.items():
        if configured:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MISE_UNPUBLISHED_DIR", "unpublished-location")
    monkeypatch.setenv("DIAGNOSTIC_TOKEN", "synthetic-credential")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "caller-environment")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "parent-plugin-data")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "ambient-root")
    monkeypatch.setenv("AGENT_TOOLKIT_STATUS_HOST_SESSION", "ambient-host")
    client = _TierClient()
    async with subject.AppServerManager(root_session_id="chosen-root") as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
        aliases: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            shared_roots, "write_host_alias", lambda root, writer, thread: aliases.append((root, writer, thread))
        )
        if operation in {"start", "reply"}:
            session = await manager.start("開始", str(tmp_path))
            if operation == "reply":
                _complete(session)
                monkeypatch.delenv("MISE_UNPUBLISHED_DIR")
                monkeypatch.setenv("NEW_REPLY_API_KEY", "reply-secret")
                client.requests.clear()
                await manager.send_message(session, "続行")
        else:
            session = await manager.resume("saved-thread", state.ResumePrompt("再開"), str(tmp_path))
        method_name = "start" if operation == "start" else "resume"
        params = next(params for method, params in client.requests if method == f"thread/{method_name}")
        server = params["config"]["mcp_servers"]["agents_server"]
        assert set(names) & set(server["env_vars"]) == (set(names) if configured else set())
        assert len(server["env_vars"]) == len(set(server["env_vars"]))
        assert set(server["env"]) == {
            "AGENT_TOOLKIT_OWNER_SESSION",
            "AGENT_TOOLKIT_STATUS_HOST_SESSION",
            "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT",
            plugin_environment.RUNTIME_DATA_ENV,
        }
        assert server["env"][plugin_environment.RUNTIME_DATA_ENV] == str(runtime_data)
        assert "UV_PROJECT_ENVIRONMENT" in server["env_vars"]
        assert "DIAGNOSTIC_TOKEN" in server["env_vars"]
        assert ("MISE_UNPUBLISHED_DIR" in server["env_vars"]) is (operation != "reply")
        assert ("NEW_REPLY_API_KEY" in server["env_vars"]) is (operation == "reply")
        assert server["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == "chosen-root"
        writer = server["env"]["AGENT_TOOLKIT_STATUS_HOST_SESSION"]
        assert writer and writer != "ambient-host"
        assert aliases == [("chosen-root", writer, session.session_id)] * (2 if operation == "reply" else 1)
        assert not {
            "CLAUDE_PLUGIN_DATA",
            "PLUGIN_DATA",
            "AGENT_TOOLKIT_OWNER_SESSION",
            "AGENT_TOOLKIT_STATUS_HOST_SESSION",
            "AGENT_TOOLKIT_DELEGATED_SESSION",
        } & set(server["env_vars"])
        encoded = json.dumps(server)
        assert not any(
            value in encoded
            for value in (
                *values.values(),
                "unpublished-location",
                "synthetic-credential",
                "reply-secret",
                "caller-environment",
                "parent-plugin-data",
            )
        )
        assert "ambient-root" not in encoded and "ambient-host" not in encoded


@pytest.mark.usefixtures("_restore_session_listeners")
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "resume", "reply"])
async def test_inner_mcp_uses_actual_app_server_parent_environment(
    operation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """内側MCPはApp Serverへ渡した環境の名前を使い、stdio子で値・空値・未設定を保持する。"""
    supplied = {
        "UNREGISTERED_VENDOR_API_KEY": "synthetic-api-value",
        "LC_ALL": "C",
        "DBUS_SESSION_BUS_ADDRESS": "synthetic-keyring-address",
        "EMPTY_CUSTOM_SETTING": "",
        "UV_PROJECT_ENVIRONMENT": "caller-env",
        "CLAUDE_CODE_SESSION_ID": "parent-context",
        "AGENT_TOOLKIT_OWNER_SESSION": "parent-owner",
        "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT": "stale-parent-plugin-root",
    }
    client = _TierClient()
    # App Serverの生成後に外側の環境が変わっても、実際の配送元は起動時のsnapshotである。
    client.parent_environment = {**host_environ(), **supplied}
    client.parent_environment.pop("MISSING_CUSTOM_SETTING", None)
    monkeypatch.setenv("NOT_IN_APP_SERVER_ENV", "not-deliverable")
    async with subject.AppServerManager(root_session_id="chosen-owner") as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
        monkeypatch.setattr(shared_roots, "write_host_alias", lambda *_args: None)
        if operation == "resume":
            session = await manager.resume("saved", state.ResumePrompt("再開"), str(tmp_path))
        else:
            session = await manager.start("開始", str(tmp_path))
            if operation == "reply":
                _complete(session)
                client.requests.clear()
                await manager.send_message(session, "続行")
        params = next(params for method, params in client.requests if method in {"thread/start", "thread/resume"})
        server = params["config"]["mcp_servers"]["agents_server"]
        assert "NOT_IN_APP_SERVER_ENV" not in server["env_vars"]
        assert "CLAUDE_CODE_SESSION_ID" not in server["env_vars"]
        # Codexのenv_varsとenvの解決順をstdio子の環境へ射影する。実CodexのMCP起動は別の受入観測で検収する。
        child_environment = {name: client.parent_environment[name] for name in server["env_vars"]}
        child_environment.update(server["env"])
        keys = [*supplied, "MISSING_CUSTOM_SETTING"]
        async with _owned_async_child(
            sys.executable,
            "-c",
            "import json, os, sys; print(json.dumps({key: os.environ.get(key) for key in sys.argv[1:]}))",
            *keys,
            env=child_environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ) as process:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            assert process.returncode == 0 and not stderr
            received = json.loads(stdout)
            assert received == {
                **supplied,
                "CLAUDE_CODE_SESSION_ID": None,
                "AGENT_TOOLKIT_OWNER_SESSION": "chosen-owner",
                "AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT": str(plugin_roots.SERVER_PLUGIN_ROOT),
                "MISSING_CUSTOM_SETTING": None,
            }
            encoded = json.dumps(server)
            assert all(value not in encoded for value in supplied.values() if value and len(value) > 1)


@pytest.mark.asyncio
async def test_agents_server_config_uses_the_stable_plugin_root() -> None:
    """内側MCPサーバーの起動コマンドは、安定した配布物rootの実体だけを指す。"""
    async with subject.AppServerManager() as manager:
        config = manager._agents_server_config("owner", "writer")

    args = config["mcp_servers"]["agents_server"]["args"]
    expected_root = plugin_roots.resolve_stable_plugin_root()
    assert args == [
        "run",
        "--no-config",
        "--no-project",
        "--python",
        "3.15",
        "python",
        str(expected_root / "scripts" / "launch.py"),
        "agent_toolkit/agents_server_mcp.py",
    ]
    assert pathlib.Path(args[-2]).is_file()
    assert (expected_root / args[-1]).is_file()


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
@pytest.mark.parametrize("owner", [None, "owner"])
@pytest.mark.asyncio
async def test_all_start_and_resume_requests_disable_experimental_context_management(
    launch_kind: state.LaunchKind, owner: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """初回依頼と追送変更を保持するため、全起動・再開リクエストで実験方式の継承を止める。"""
    client = _TierClient()
    async with subject.AppServerManager(root_session_id=owner) as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
        await manager.start("初回依頼", str(tmp_path), launch_kind=launch_kind)
        await manager.resume("tier-thread", state.ResumePrompt("追送変更"), str(tmp_path), launch_kind=launch_kind)
        requests = [(method, params) for method, params in client.requests if method in {"thread/start", "thread/resume"}]
        assert [method for method, _params in requests] == ["thread/start", "thread/resume"]
        for _method, params in requests:
            features = params["config"]["features"]
            assert features["context_management"]["experimental_mode"] is False
            if launch_kind == "explore":
                assert features["plugins"] is False
                assert features["hooks"] is True
            else:
                assert "hooks" not in params["config"]


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
@pytest.mark.parametrize("windows", [False, True])
async def test_start_and_resume_pass_the_same_stable_plugin_root_to_all_launch_kinds(
    launch_kind: state.LaunchKind, windows: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """開始と再開の全役割へ、実在する同じplugin rootと、plugin rootの再解決を禁じる指示を渡す。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True, root_is_version=True)
    (source / ".mcp.json").write_text(
        (plugin_roots.SERVER_PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (source / "hooks").mkdir()
    (source / "hooks/hooks.codex.json").write_text(
        (plugin_roots.SERVER_PLUGIN_ROOT / "hooks/hooks.codex.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (source / "skills" / "sample").mkdir(parents=True)
    (source / "skills" / "sample" / "SKILL.md").write_text("# 手順\n", encoding="utf-8")
    plugin_root = plugin_roots.resolve_stable_plugin_root(source)
    shutil.rmtree(source)
    monkeypatch.setattr(plugin_roots, "SERVER_PLUGIN_ROOT", plugin_root)
    cli_instructions = plugin_environment.cli_instructions
    monkeypatch.setattr(plugin_environment, "cli_instructions", lambda root: cli_instructions(root, windows=windows))
    client = _TierClient()
    async with subject.AppServerManager(root_session_id="owner") as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))

        await manager.start("開始", str(tmp_path), launch_kind=launch_kind)
        start_params = next(params for method, params in client.requests if method == "thread/start")
        client.requests.clear()
        await manager.resume("tier-thread", state.ResumePrompt("再開"), str(tmp_path), launch_kind=launch_kind)
        resume_params = next(params for method, params in client.requests if method == "thread/resume")

        for params in (start_params, resume_params):
            assert params["baseInstructions"] == subject.SUBAGENT_BASE_INSTRUCTIONS
            instructions = params["developerInstructions"]
            assert str(plugin_root) in instructions
            command = next(
                line.removeprefix("起動部分: ") for line in instructions.splitlines() if line.startswith("起動部分: ")
            )
            entry = plugin_root / "scripts" / ("atk.cmd" if windows else "atk")
            assert entry.is_file()
            if windows:
                assert command == "& '" + str(entry).replace("'", "''") + "'"
            else:
                assert shlex.split(command) == [str(entry)]
            assert (subject.python_runtime_instructions() in instructions) is (launch_kind != "delegate")
            args = params["config"]["mcp_servers"]["agents_server"]["args"]
            assert pathlib.Path(args[-2]) == plugin_root / "scripts" / "launch.py"
            assert pathlib.Path(args[-2]).is_file()
            assert (plugin_root / args[-1]).is_file()
            assert (plugin_root / "skills" / "sample" / "SKILL.md").is_file()
            assert "別hostのcache版数から別のplugin rootを組み立てない" in instructions
        assert start_params["developerInstructions"] == resume_params["developerInstructions"]


def test_developer_instructions_include_user_rules_except_embedded(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`~/.claude/rules/`配下の規範ファイルを委譲先の指示へ連結し、全体指示ファイルが埋め込むものだけを除く。

    連結しないとCodexの委譲先はClaudeの委譲先が受け取るホスト固有の制約を知らずに操作し、
    埋め込み済みの本文を重ねると指示が重複する。起動時に読んだ値を使い回すと、ユーザーの編集が再開へ反映されない。
    """
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(plugin_roots, "SERVER_PLUGIN_ROOT", tmp_path / "plugin")
    rules = home / ".claude" / "rules"
    (rules / "agent-toolkit").mkdir(parents=True)
    plain = rules / "env.local.md"
    plain.write_text("# env.local.md\n\nGitHubへ書き込まない。\n", encoding="utf-8")
    nested = rules / "team" / "review.md"
    nested.parent.mkdir()
    nested.write_text("# review.md\n\nレビューは日本語で書く。\n", encoding="utf-8")
    embedded = rules / "agent-toolkit" / "01-agent.md"
    embedded.write_text(
        '<atk-auto source="agent-toolkit" kind="rules" path="agent-toolkit/share/rules/common/01-agent.md">\n'
        "埋め込み済みの本文\n</atk-auto>\n",
        encoding="utf-8",
    )
    unmatched = rules / "myprojects.md"
    unmatched.write_text(
        '<atk-auto source="dotfiles" kind="rules" path=".chezmoi-source/dot_claude/rules/myprojects.md.tmpl">\n'
        "Claude Code向けの一覧\n</atk-auto>\n",
        encoding="utf-8",
    )
    codex_home.mkdir()
    (codex_home / "AGENTS.md").write_text(
        "# 全体指示\n\n"
        '<atk-auto source="agent-toolkit" kind="rules" '
        'path="agent-toolkit/share/rules/common/01-agent.md">\n本文\n</atk-auto>\n',
        encoding="utf-8",
    )

    for launch_kind in ("delegate", "explore", "write", "shell"):
        instructions = subject._developer_instructions(launch_kind)
        assert "GitHubへ書き込まない。" in instructions
        assert "レビューは日本語で書く。" in instructions
        assert "Claude Code向けの一覧" in instructions
        assert "埋め込み済みの本文" not in instructions
        for path in (plain, nested, unmatched):
            assert f'kind="user-rules" path="{path}"' in instructions

    plain.write_text("# env.local.md\n\n書き換えた制約。\n", encoding="utf-8")
    rewritten = subject._developer_instructions("delegate")
    assert "書き換えた制約。" in rewritten
    assert "GitHubへ書き込まない。" not in rewritten


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_start_uses_noninteractive_policy_and_shared_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codex開始時の承認・sandbox固定値と公開射影を検証する。"""
    async with subject.AppServerManager() as manager:
        client = FakeCodexClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> FakeCodexClient:
            return client

        monkeypatch.setattr(manager, "_ensure_client", ensure_client)
        session = await manager.start("調査", str(tmp_path), "gpt-test", "high")
        assert session.status == "starting"
        thread_start = client.requests[0][1]
        turn_start = client.requests[1][1]
        assert thread_start["approvalPolicy"] == "never"
        assert thread_start["sandbox"] == "danger-full-access"
        assert turn_start["sandboxPolicy"] == {"type": "dangerFullAccess"}
        assert turn_start["model"] == "gpt-test"
        assert turn_start["effort"] == "high"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_explore_commands_reach_start_resume_and_followup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """探索の開始・再開・追送で通常のコマンドとMCP設定を保持する。"""
    async with subject.AppServerManager() as normal_manager:
        normal_client = FakeCodexClient()

        async def ensure_normal_client(*_args: Any, **_kwargs: Any) -> FakeCodexClient:
            return normal_client

        monkeypatch.setattr(normal_manager, "_ensure_client", ensure_normal_client)
        await normal_manager.start("調査", str(tmp_path), "model", "high")

        async with subject.AppServerManager() as explore_manager:
            explore_client = _TierClient()

            async def ensure_explore_client(*_args: Any, **_kwargs: Any) -> _TierClient:
                return explore_client

            monkeypatch.setattr(explore_manager, "_ensure_client", ensure_explore_client)
            session = await explore_manager.start("調査", str(tmp_path), "model", "high", launch_kind="explore")
            await explore_manager._start_turn(session, "追送", cast(subject.JsonRpcProcess, explore_client))
            await explore_manager._resume_thread(session, explore_client)

            normal_thread = normal_client.requests[0][1]
            explore_thread = explore_client.requests[0][1]
            assert normal_thread["config"] == {
                "bypass_hook_trust": True,
                "features": {"context_management": {"experimental_mode": False}},
            }
            assert normal_thread["developerInstructions"] == subject._developer_instructions("delegate")  # noqa: SLF001
            assert explore_thread["config"]["bypass_hook_trust"] is True
            assert explore_thread["config"]["project_doc_max_bytes"] == 0
            assert explore_thread["config"]["features"] == {
                "plugins": False,
                "hooks": True,
                "context_management": {"experimental_mode": False},
            }
            # 軽量起動にも出力省略の回収案内を届けるためPost hookを接続し、本文配送用の退避上限の解除は渡さない。
            configured = explore_thread["config"]["hooks"]["PostToolUse"][0]
            assert configured["matcher"] == "Bash"
            assert all("additionalContextLimit" not in handler for handler in configured["hooks"])
            assert all("${PLUGIN_ROOT}" not in handler["command"] for handler in configured["hooks"])
            pyfltr = explore_thread["config"]["mcp_servers"]["pyfltr"]
            definition = json.loads((subject.plugin_roots.SERVER_PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))[
                "mcpServers"
            ]["pyfltr"]
            assert pyfltr["command"] == definition["command"]
            assert pyfltr["args"] == [
                arg.replace("${CLAUDE_PLUGIN_ROOT}", str(subject.plugin_roots.SERVER_PLUGIN_ROOT)) for arg in definition["args"]
            ]
            assert explore_thread["developerInstructions"] == subject._developer_instructions("explore")  # noqa: SLF001
            for method, params in explore_client.requests:
                assert params["approvalPolicy"] == "never"
                if method in {"thread/start", "thread/resume"}:
                    assert params["sandbox"] == "danger-full-access"
                    assert params["config"] == explore_thread["config"]
                else:
                    assert params["sandboxPolicy"] == {"type": "dangerFullAccess"}
            assert normal_client.requests[1][1]["sandboxPolicy"] == {"type": "dangerFullAccess"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_shell_start_shares_explore_thread_conditions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codexのシェル実行起動は探索と同じthread条件で開始し、指示だけを実行専用へ替える。"""
    async with subject.AppServerManager() as manager:
        client = FakeCodexClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> FakeCodexClient:
            return client

        monkeypatch.setattr(manager, "_ensure_client", ensure_client)
        await manager.start("make test", str(tmp_path), "model", "high", launch_kind="shell")

        thread_params = client.requests[0][1]
        assert thread_params["config"]["bypass_hook_trust"] is True
        assert thread_params["config"]["project_doc_max_bytes"] == 0
        # shellはpluginのhookをそのまま受けるため、thread設定でPost hookを重ねない。
        assert thread_params["config"]["features"] == {"context_management": {"experimental_mode": False}}
        assert "hooks" not in thread_params["config"]
        assert thread_params["developerInstructions"] == subject._developer_instructions("shell")  # noqa: SLF001


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_resume_passes_delegate_instructions(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Codexの再開は`mode`に応じた委譲先宣言をthread/resumeへ渡す。"""
    client = _TierClient()
    async with subject.AppServerManager() as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
        normal_session = state.SessionState("thread-normal", str(tmp_path), engine="codex")
        explore_session = state.SessionState("thread-explore", str(tmp_path), engine="codex", launch_kind="explore")
        shell_session = state.SessionState("thread-shell", str(tmp_path), engine="codex", launch_kind="shell")

        for session in (normal_session, explore_session, shell_session):
            await manager.resume(session.session_id, state.ResumePrompt("続行"), session.cwd, launch_kind=session.launch_kind)

        normal_resume, explore_resume, shell_resume = [
            params for method, params in client.requests if method == "thread/resume"
        ]
        assert normal_resume["developerInstructions"] == subject._developer_instructions("delegate")  # noqa: SLF001
        assert normal_resume["config"] == {
            "bypass_hook_trust": True,
            "features": {"context_management": {"experimental_mode": False}},
        }
        assert explore_resume["developerInstructions"] == subject._developer_instructions("explore")  # noqa: SLF001
        assert explore_resume["config"]["project_doc_max_bytes"] == 0
        assert explore_resume["config"]["features"] == {
            "plugins": False,
            "hooks": True,
            "context_management": {"experimental_mode": False},
        }
        definition = json.loads((subject.plugin_roots.SERVER_PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))[
            "mcpServers"
        ]["pyfltr"]
        assert explore_resume["config"]["mcp_servers"]["pyfltr"]["command"] == definition["command"]
        assert shell_resume["developerInstructions"] == subject._developer_instructions("shell")  # noqa: SLF001
        assert shell_resume["config"]["bypass_hook_trust"] is True
        assert shell_resume["config"]["project_doc_max_bytes"] == 0
        assert shell_resume["config"]["features"] == {"context_management": {"experimental_mode": False}}
        assert "hooks" not in shell_resume["config"]
