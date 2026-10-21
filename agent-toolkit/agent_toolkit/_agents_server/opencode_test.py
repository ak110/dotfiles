"""公開CLIを模した子プロセスでOpenCodeの開始・継続・終端・失敗と規範配送を検証する。"""

import asyncio
import json
import os
import pathlib
import stat
import sys
from typing import Any

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, opencode, session_errors
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import state_paths
from agent_toolkit._testing import agents_server_support
from agent_toolkit._testing.isolation import restore_host_environment

_CREATE_PROCESS = asyncio.create_subprocess_exec

_FAKE = """#!{python}
import json
import os
import sys

args = sys.argv[1:]
session = args[args.index("--session") + 1] if "--session" in args else "ses_example"
prompt = args[-1]
with open(os.environ["OPENCODE_CAPTURE"], "a", encoding="utf-8") as capture:
    capture.write(json.dumps(args, ensure_ascii=False) + "\\n")
def emit(kind, **values):
    print(json.dumps(dict(type=kind, sessionID=session, **values)), flush=True)
emit("step_start", part=dict(type="step-start"))
if prompt.endswith("auth-failure") or "\\nauth-failure\\n" in prompt:
    emit("error", error=dict(name="ProviderAuthError", data=dict(message="authentication failed")))
    sys.exit(1)
if prompt.endswith("no-output") or "\\nno-output\\n" in prompt:
    sys.exit(0)
emit("text", part=dict(type="text", text=prompt[-8:]))
emit("step_finish", part=dict(type="step-finish", reason="stop", cost=0.01, tokens=dict(total=30, input=20, output=10)))
"""


def _install_cli(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """ホストのPythonから起動する偽CLIと、その引数の保存先を用意する。"""
    restore_host_environment(monkeypatch)
    directory = tmp_path / "bin"
    directory.mkdir()
    script = directory / "opencode"
    script.write_text(_FAKE.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    if os.name == "nt":
        original = opencode.build_command
        monkeypatch.setattr(
            opencode.OpenCodeManager,
            "build_command",
            staticmethod(lambda *args: [sys.executable, str(script), *original(*args)[1:]]),
        )
    capture = tmp_path / "argv.jsonl"
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("OPENCODE_CAPTURE", str(capture))
    monkeypatch.setattr(opencode.host_homes, "claude_config_dir", lambda: tmp_path / "claude")
    return capture


async def _finished(manager: opencode.OpenCodeManager, session: SessionState) -> None:
    """backendの終端通知を、有限の待機で受け取る。"""
    async with manager._condition:  # pylint: disable=protected-access
        await asyncio.wait_for(manager._condition.wait_for(lambda: session.terminal), timeout=10)  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_cli_start_reply_and_raw_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じIDで継続し、モデルとvariantを分離して渡し、元のイベントを両turnとも保存する。"""
    capture = _install_cli(tmp_path, monkeypatch)
    async with opencode.OpenCodeManager(log_directory=tmp_path / "logs") as manager:
        session = await manager.start("最初の依頼を処理", str(tmp_path), "local/qwen", "high")
        await _finished(manager, session)
        assert session.status == "completed"
        assert session.engine == "opencode"
        assert session.agent_message.endswith("最初の依頼を処理")
        result = await manager.send_message(session, "継続の依頼を処理")
        assert result["delivery"] == "reply_started"
        await _finished(manager, session)
        assert session.status == "completed"
        assert session.agent_message.endswith("継続の依頼を処理")
        assert session.turn_seq == 2
    commands = [json.loads(line) for line in capture.read_text(encoding="utf-8").splitlines()]
    assert "--session" not in commands[0]
    assert commands[1][commands[1].index("--session") + 1] == "ses_example"
    for command in commands:
        assert command[command.index("--model") + 1] == "local/qwen"
        assert command[command.index("--variant") + 1] == "high"
        assert command[command.index("--format") + 1] == "json"
        assert "--auto" in command
    records = [json.loads(line) for line in (tmp_path / "logs" / "ses_example.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["agent_toolkit_input", "step_start", "text", "step_finish"] * 2
    assert all(record["sessionID"] == "ses_example" for record in records if record["type"] != "agent_toolkit_input")
    assert records[0]["model"] == "local/qwen"
    assert records[0]["prompt"].endswith("最初の依頼を処理")
    assert records[4]["prompt"].endswith("継続の依頼を処理")


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", ["auth-failure", "no-output"])
async def test_failed_turn_is_available_for_candidate_exclusion(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    """認証失敗と本文なしの終了を、完了として受理せず候補の不成立として返す。"""
    _install_cli(tmp_path, monkeypatch)
    async with opencode.OpenCodeManager() as manager:
        session = await manager.start(prompt, str(tmp_path), "local/qwen")
        await _finished(manager, session)
        assert session.status == "failed"
        assert manager.unavailable_reason(session)


@pytest.mark.asyncio
async def test_missing_cli_and_missing_model_fail_start(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI不在とモデル未指定を起動時の可用性の失敗として返す。"""
    monkeypatch.setenv("PATH", str(tmp_path / "absent"))
    monkeypatch.setattr(opencode.host_homes, "claude_config_dir", lambda: tmp_path / "claude")
    async with opencode.OpenCodeManager() as manager:
        with pytest.raises(session_errors.DelegateBackendError, match="failed to start OpenCode CLI"):
            await manager.start("依頼", str(tmp_path), "local/qwen")
        with pytest.raises(session_errors.DelegateBackendError, match="モデルが未設定"):
            await manager.start("依頼", str(tmp_path))


@pytest.mark.asyncio
async def test_cli_receives_all_plugin_and_user_rules(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """実行する子へ、共通規範・委譲先規範・ユーザーの再帰的な規範集合を届ける。"""
    capture = _install_cli(tmp_path, monkeypatch)
    custom = tmp_path / "claude" / "rules" / "nested" / "custom.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("この規範の配送を観測する。", encoding="utf-8")
    async with opencode.OpenCodeManager() as manager:
        session = await manager.start("規範の配送を確認", str(tmp_path), "local/qwen")
        await _finished(manager, session)
    prompt = json.loads(capture.read_text(encoding="utf-8").splitlines()[0])[-1]
    root = opencode.plugin_roots.SERVER_PLUGIN_ROOT
    expected = [*sorted((root / "rules").glob("*.md")), root / "share" / "rules-subagent.md", custom]
    assert expected
    for path in expected:
        assert path.read_text(encoding="utf-8") in prompt


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("case", ["empty", "ready", "auth-failure"])
async def test_public_start_uses_configured_candidate_and_fallback(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """公開startで空段位を除外し、設定済みCLIを選び、認証失敗なら次engineへ進む。"""
    capture = _install_cli(tmp_path, monkeypatch)
    script = capture.parent / "bin" / "opencode"

    async def launch_fake(*args: str, **kwargs: Any) -> asyncio.subprocess.Process:
        if args[0] == sys.executable:
            assert args[1] == str(script)
            return await _CREATE_PROCESS(*args, **kwargs)
        assert args[:2] == ("opencode", "run")
        return await _CREATE_PROCESS(sys.executable, str(script), *args[1:], **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch_fake)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_ENGINE", "opencode,claude")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_OPENCODE_LOW_TIER_MODEL", "" if case == "empty" else "local/qwen/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_LOW_TIER_MODEL", "test/medium")

    class ObservedBackend(agents_server_support.FakeBackend):
        async def start(self, *args: Any, **kwargs: Any) -> SessionState:
            session = await super().start(*args, **kwargs)
            session.model_output_observed = True
            return session

    async with server_manager.AgentsServerManager(None) as current:
        actual = opencode.OpenCodeManager(current.sessions, current._condition)  # pylint: disable=protected-access
        fallback = ObservedBackend(current.sessions, "claude")
        agents_server_support.install_backend(current, "opencode", actual)
        agents_server_support.install_backend(current, "claude", fallback)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        response = await mcp_tools.start(mode="explore", cwd=str(tmp_path), prompt=case)
        assert response["launch_identity"]["engine"] == ("opencode" if case == "ready" else "claude")
        assert len(fallback.start_calls) == (0 if case == "ready" else 1)
        assert capture.exists() is (case != "empty")
        if case == "auth-failure":
            assert response["excluded_candidates"][0]["engine"] == "opencode"
            assert "authentication failed" in response["excluded_candidates"][0]["reason"]
