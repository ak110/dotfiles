"""claudeのoptionsの契約を検証する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import claude, claude_options, launch_prompts, state
from agent_toolkit._common import claude_connection as claude_settings
from agent_toolkit._common import plugin_environment
from agent_toolkit._testing.agents_server_claude_support import _capture_options, _lightweight_environment
from agent_toolkit._testing.agents_server_support import FakeClaudeClient, ResultMessage, SystemMessage


@pytest.mark.parametrize("settings_kind", ["none", "json", "file", "missing", "invalid", "array"])
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
def test_build_options_inherits_parent_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    settings_kind: str,
    launch_kind: claude.LaunchKind,
) -> None:
    """全起動区分で親のsettingsのenv以外の項目を保ち、SDKへ付与する全envを親のenvより優先する値としてsettingsにも書く。"""
    captured: dict[str, object] = {}

    class Options:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.settings = kwargs["settings"]

    parent = {"env": {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "OTHER": "secret-parent-value"}, "disableAllHooks": True}
    path = tmp_path / "parent.json"
    path.write_text(json.dumps(parent), encoding="utf-8")
    settings = {
        "none": None,
        "json": json.dumps(parent),
        "file": str(path),
        "missing": str(tmp_path / "missing"),
        "invalid": "{bad-json",
        "array": "[]",
    }[settings_kind]

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", types.SimpleNamespace(ClaudeAgentOptions=Options))
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: settings)
    monkeypatch.setattr(claude_settings, "state_dir", lambda: tmp_path / "state")

    options = claude._build_options(str(tmp_path), "model", "medium", launch_kind=launch_kind, root_session_id="root")
    merged = captured["settings"]
    assert isinstance(merged, str)
    if isinstance(merged, claude_settings.SettingsFile):
        merged_path = pathlib.Path(merged)
        assert merged_path.stat().st_mode & 0o077 == 0
        value = json.loads(merged_path.read_text(encoding="utf-8"))
        assert "secret-parent-value" not in merged
    else:
        value = json.loads(merged)
    env = captured["env"]
    assert isinstance(env, dict)
    assert all(value["env"][key] == item for key, item in env.items())
    if settings_kind in {"json", "file"}:
        assert value["env"]["OTHER"] == "secret-parent-value"
        assert value["disableAllHooks"] is True
    else:
        assert set(value) == {"env"}
    claude_settings.cleanup_settings(options)
    if isinstance(merged, claude_settings.SettingsFile):
        assert not pathlib.Path(merged).exists()


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
@pytest.mark.parametrize("session_id", [None, "saved"])
def test_build_options_buffer_and_skill_contract(
    monkeypatch: pytest.MonkeyPatch, launch_kind: claude.LaunchKind, session_id: str | None
) -> None:
    captured = _capture_options(monkeypatch)
    claude._build_options("/repo", None, None, session_id, launch_kind)
    buffer_size = captured["max_buffer_size"]
    assert isinstance(buffer_size, int) and buffer_size > 1073741824
    assert captured.get("disallowed_tools", []) == ([] if launch_kind == "delegate" else ["Skill"])


def test_build_options_keeps_every_launch_out_of_bypass_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動区分によらずbypass系以外の権限モードで起動する。

    bypass系の受信側はセッション間メッセージを保留するため、起動したセッションが初期化を完了できない。
    """
    captured = _capture_options(monkeypatch)

    # 受理しない値を意図的に渡すテストのため、静的な型判定の対象から外す。
    claude._build_options("/tmp", "model", "medium", launch_kind="delegate")  # pylint: disable=protected-access

    assert captured["permission_mode"] == "auto"


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
def test_build_options_loads_user_hooks_for_every_launch(
    monkeypatch: pytest.MonkeyPatch,
    launch_kind: claude.LaunchKind,
) -> None:
    """全起動区分でユーザー設定のplugin hookを読み、軽量起動の道具を限定し、部分出力を受け取る。

    部分出力を受け取らないと、`start`の可用性待機を打ち切るAPIの応答開始が届かない。
    """
    captured = _capture_options(monkeypatch)

    claude._build_options(  # pylint: disable=protected-access
        "/tmp", "model", "medium", launch_kind=launch_kind
    )

    assert captured["setting_sources"] == (["user", "project"] if launch_kind == "delegate" else ["user"])
    assert captured["include_partial_messages"] is True
    if launch_kind != "delegate":
        assert captured["skills"] == []
        assert captured["allowed_tools"] == claude._LAUNCH_ALLOWED_TOOLS[launch_kind]  # pylint: disable=protected-access


def test_build_options_passes_debug_file_to_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """診断ログの保存先を委譲先CLIの引数として渡す。"""
    captured = _capture_options(monkeypatch)
    debug_file = tmp_path / "delegate.log"

    claude._build_options("/tmp", "model", "medium", debug_file=debug_file)  # pylint: disable=protected-access

    assert captured["extra_args"] == {"debug-file": str(debug_file)}


@pytest.mark.parametrize("cli_path", ["/opt/claude/bin/claude", None])
def test_build_options_resolves_cli_from_path(monkeypatch: pytest.MonkeyPatch, cli_path: str | None) -> None:
    """PATHで見つかったCLIだけをSDKへ渡す。"""
    captured = _capture_options(monkeypatch)
    monkeypatch.setattr(claude.shutil, "which", lambda _name: cli_path)

    claude._build_options("/tmp", "model", "medium")  # pylint: disable=protected-access

    assert captured.get("cli_path") == cli_path
    assert ("cli_path" in captured) is (cli_path is not None)


def test_build_options_omits_debug_file_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存先を指定しない起動では診断ログの引数を渡さない。"""
    captured = _capture_options(monkeypatch)

    claude._build_options("/tmp", "model", "medium")  # pylint: disable=protected-access

    assert "extra_args" not in captured


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.usefixtures("_owner_session_environment")
async def test_claude_options_use_owned_base_instructions(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Agent SDKへ自前の基本指示と設定読込元、解決した所有セッションを渡す。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "owner-session")
    monkeypatch.setenv("PATH", os.defpath)

    options = claude._build_options(str(tmp_path), "model", "high", root_session_id="owner-session")
    assert options.system_prompt == (
        f"{launch_prompts.SUBAGENT_BASE_INSTRUCTIONS}\n{launch_prompts.CLAUDE_DELEGATE_SYSTEM_PROMPT}\n"
        f"{launch_prompts.AUTO_RESUME_NOTICE}\n"
        f"{launch_prompts.cli_runtime_instructions(claude.plugin_roots.SERVER_PLUGIN_ROOT)}"
    )
    assert options.setting_sources == ["user", "project"]
    assert options.permission_mode == "auto"
    assert options.env == {
        "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
        "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND": "delegate",
        "PATH": str(claude.plugin_roots.SERVER_PLUGIN_ROOT / "scripts") + os.pathsep + os.environ["PATH"],
        plugin_environment.RUNTIME_DATA_ENV: str(plugin_environment.runtime_data(os.environ)),
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
        "AGENT_TOOLKIT_OWNER_SESSION": "owner-session",
        "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
    }


@pytest.mark.usefixtures("agents_server_isolation")
def test_claude_options_accept_saved_session_id(tmp_path: pathlib.Path) -> None:
    """Claude SDK optionsへ保存済みsession IDをresumeとして渡す。"""
    options = claude._build_options(str(tmp_path), "model", "high", "claude-saved")
    assert options.resume == "claude-saved"


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
@pytest.mark.skipif(os.name == "nt", reason="POSIXの実ラッパー起動はPOSIXホストで検証する")
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_sdk_start_and_resume_deliver_executable_cli_without_old_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launch_kind: state.LaunchKind,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """SDKへ届くCLI案内とPATHが同じ配布物を選び、呼び出し元の環境と所有者を保つ。

    SDKの外部モデルは代替し、実ラッパーとscripts/launch.pyを経た準備済みPythonで引数・環境と終了コードを観測する。
    依存導入とWindowsラッパーの実行は、この試験の観測範囲に含めない。
    """
    bash = shutil.which("bash", path=host_environ()["PATH"])
    assert bash is not None
    tools = tmp_path / "user tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "if sys.argv[1:4] == ['run', '--no-config', '--no-project']:\n"
        "    os.execv(sys.executable, [sys.executable, '-B', *sys.argv[7:]])\n"
        "print(json.dumps({'args': sys.argv[1:], 'path': os.environ['PATH'], "
        "'data': os.environ['AGENT_TOOLKIT_RUNTIME_DATA'], "
        "'environment': os.environ['UV_PROJECT_ENVIRONMENT'], "
        "'runtime': os.environ['AGENT_TOOLKIT_RUNTIME_ENVIRONMENT']}))\n"
        "print('uv-observed', file=sys.stderr)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    original_path = os.pathsep.join((str(tools), str(pathlib.Path(bash).parent), "/usr/bin", "/bin"))
    data = tmp_path / "plugin data"
    monkeypatch.setenv("PATH", original_path)
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(data))
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: None)
    root = claude.plugin_roots.SERVER_PLUGIN_ROOT
    prepared = plugin_environment.project_environment(root, os.environ)
    prepared_python = plugin_environment.python_path(prepared)
    prepared_python.parent.mkdir(parents=True)
    prepared_python.write_text(uv.read_text(encoding="utf-8"), encoding="utf-8")
    prepared_python.chmod(0o755)
    (prepared / ".agent-toolkit-dependencies-ready").write_text("ready\n", encoding="utf-8")
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        return FakeClaudeClient([[SystemMessage("cli-session"), ResultMessage("完了")]])

    async with claude.ClaudeServerManager(client_factory=factory, root_session_id="explicit-owner") as manager:
        await manager.start("開始", str(tmp_path), launch_kind=launch_kind)
        await manager.resume("cli-session", state.ResumePrompt("再開"), str(tmp_path), launch_kind=launch_kind)
    assert len(options_seen) == 2
    for options in options_seen:
        assert options.resume in {None, "cli-session"}
        assert options.plugins == [{"type": "local", "path": str(root)}]
        assert options.env["AGENT_TOOLKIT_OWNER_SESSION"] == "explicit-owner"
        assert options.env["AGENT_TOOLKIT_DELEGATED_SESSION"] == "1"
        assert options.env["PATH"].split(os.pathsep) == [str(root / "scripts"), *original_path.split(os.pathsep)]
        assert options.env[plugin_environment.RUNTIME_DATA_ENV] == str(data)
        assert options.permission_mode == "auto"
        prompt = options.system_prompt if isinstance(options.system_prompt, str) else options.system_prompt["append"]
        command = next(line.removeprefix("起動部分: ") for line in prompt.splitlines() if line.startswith("起動部分: "))
        assert shlex.split(command) == [str(root / "scripts" / "atk")]
        environment = {**host_environ(), **options.env}
        assert shutil.which("atk", path=environment["PATH"]) == str(root / "scripts" / "atk")
        result = subprocess.run(
            [bash, "-c", command + " --help"],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert result.returncode == 7, result.stderr
        observed = json.loads(result.stdout)
        assert observed["args"] == [str(root / "agent_toolkit" / "atk.py"), "--help"]
        assert observed["data"] == str(data)
        selected_environment = pathlib.Path(observed["environment"])
        assert selected_environment.parent == data / "environments"
        assert not selected_environment.is_relative_to(root)
        assert observed["runtime"] == str(selected_environment)
        assert selected_environment == prepared
        assert prepared_python.is_file()
        assert observed["path"] == environment["PATH"]
        assert result.stderr == "uv-observed\n"
    assert os.environ["PATH"] == original_path


@pytest.mark.parametrize("session_id", [None, "saved-session"])
@pytest.mark.usefixtures("agents_server_isolation")
def test_explore_options_keep_search_tools_and_mcp_on_resume(tmp_path: pathlib.Path, session_id: str | None) -> None:
    """新規・再接続の両方でコマンド検索とpyfltrへ到達し、診断ログの指定を保つ。"""
    options = claude._build_options(str(tmp_path), "model", "high", session_id, "explore", debug_file=tmp_path / "debug.log")
    assert options.tools == {"type": "preset", "preset": "claude_code"}
    assert set(options.allowed_tools) == {"Read", "Glob", "Grep", "Bash", "mcp__pyfltr__*"}
    assert options.permission_mode == "auto"
    assert not options.hooks
    assert options.disallowed_tools == ["Skill"]
    assert options.extra_args == {
        "debug-file": str(tmp_path / "debug.log"),
        "permission-prompts": "none",
    }
    assert launch_prompts.python_runtime_instructions() in options.system_prompt


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.usefixtures("_owner_session_environment")
def test_claude_explore_options_reduce_instruction_sources_and_keep_tools(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude探索起動はユーザー設定のhookを読み、探索用toolと指示を明示する。

    所有セッションを解決できない環境ではそのキーを設定しない。
    """
    monkeypatch.setenv("PATH", os.defpath)
    options = claude._build_options(str(tmp_path), "model", "high", launch_kind="explore")
    assert options.setting_sources == ["user"]
    assert options.skills == []
    assert options.env == _lightweight_environment("explore")
    assert options.system_prompt == (
        f"{launch_prompts.CLAUDE_EXPLORE_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}\n"
        f"{launch_prompts.python_runtime_instructions()}\n"
        f"{launch_prompts.cli_runtime_instructions(claude.plugin_roots.SERVER_PLUGIN_ROOT)}"
    )
    assert options.tools == {"type": "preset", "preset": "claude_code"}
    assert set(options.allowed_tools) == {"Read", "Glob", "Grep", "Bash", "mcp__pyfltr__*"}
    assert set(options.mcp_servers) == {"pyfltr"}
    assert options.extra_args == {"permission-prompts": "none"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.usefixtures("_owner_session_environment")
def test_claude_write_options_limit_lightweight_session_to_file_edits(tmp_path: pathlib.Path) -> None:
    """Claude軽量書込は汎用コマンドを許可せず、固定プロンプトと編集toolだけを使う。"""
    options = claude._build_options(str(tmp_path), "model", "high", launch_kind="write")
    assert options.setting_sources == ["user"]
    assert options.skills == []
    assert options.system_prompt == (
        f"{launch_prompts.WRITE_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}\n"
        f"{launch_prompts.python_runtime_instructions()}\n"
        f"{launch_prompts.cli_runtime_instructions(claude.plugin_roots.SERVER_PLUGIN_ROOT)}"
    )
    assert set(options.allowed_tools) == {"Read", "Glob", "Grep", "Write", "Edit"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.usefixtures("_owner_session_environment")
def test_claude_shell_options_share_lightweight_launch_with_command_tools(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claudeのシェル実行起動は探索と同じ軽量条件を共有し、実行用toolと指示を選ぶ。"""
    monkeypatch.setenv("PATH", os.defpath)
    options = claude._build_options(str(tmp_path), "model", "high", launch_kind="shell")
    assert options.setting_sources == ["user"]
    assert options.skills == []
    assert options.env == _lightweight_environment("shell")
    assert options.system_prompt == (
        f"{launch_prompts.SHELL_SYSTEM_PROMPT}\n{launch_prompts.AUTO_RESUME_NOTICE}\n"
        f"{launch_prompts.python_runtime_instructions()}\n"
        f"{launch_prompts.cli_runtime_instructions(claude.plugin_roots.SERVER_PLUGIN_ROOT)}"
    )
    assert set(options.allowed_tools) == {"Bash", "Read"}
