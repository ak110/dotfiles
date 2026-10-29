"""接続と記録先の起動契約を、backendから子環境への受け渡しで検証する。"""

import asyncio
import contextlib
import json
import os
import pathlib
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import child_environment, claude, codex, launch_requests, resume_waits, session_handoff, state
from agent_toolkit._atk import claude_connection
from agent_toolkit._common import delegated_session
from agent_toolkit._common.claude_usage_limit import UsageLimitState
from agent_toolkit._testing.agents_server_support import AssistantMessage, QueueClaudeClient, configure_claude_payg
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


async def _ignore_message(_value: dict[str, Any]) -> None:
    """起動失敗までの試験で使う非同期受信先。"""


@pytest.mark.parametrize("kind", ["subscription", "payg", "inherit"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.asyncio
async def test_sdk_child_environment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, kind: str, configured: bool
) -> None:
    """SDK環境とsettingsを合成した子環境で、設定ファイルが指定するキーだけが空になる。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=api-key\nAWS_PROFILE=api-profile\nCUSTOM_CONNECTION=api-value\n")
    configure_claude_payg(monkeypatch, str(api))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "user"))
    if configured:
        (tmp_path / "user").mkdir()
        (tmp_path / "user/settings.json").write_text(json.dumps({"env": {"AWS_PROFILE": "settings-profile"}}))
    monkeypatch.setenv("AWS_PROFILE", "parent-profile")
    monkeypatch.setenv("CUSTOM_CONNECTION", "parent-value")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    child_environment.prepare(os.environ)
    selected = claude_connection.Connection(kind, str(api) if kind == "payg" else None, str(api))
    monkeypatch.setattr(claude_connection, "candidates", lambda *_args, **_kwargs: claude_connection.Candidates((selected,)))
    effective: list[dict[str, str]] = []

    def factory(options: Any) -> QueueClaudeClient:
        merged = json.loads(
            pathlib.Path(options.settings).read_text() if not options.settings.startswith("{") else options.settings
        )
        effective.append({**os.environ, **options.env, **merged["env"]})
        return QueueClaudeClient(
            [[claude_agent_sdk.SystemMessage("init", {"session_id": "sdk-env"}), AssistantMessage("開始")]]
        )

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        await backend.start("調査", str(tmp_path))
    values = effective[0]
    if kind == "subscription":
        assert "CUSTOM_CONNECTION" not in values
        assert values.get("AWS_PROFILE") == ("" if configured else None)
        assert values["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"
    elif kind == "payg":
        assert values["AWS_PROFILE"] == "api-profile"
        assert values["CUSTOM_CONNECTION"] == "api-value"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in values
    else:
        assert values["AWS_PROFILE"] == "parent-profile"
        assert values["CUSTOM_CONNECTION"] == "parent-value"


@pytest.mark.parametrize("initial", ["subscription", "inherit"])
@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.asyncio
async def test_api_only_continues_same_conversation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, initial: str, cold: bool
) -> None:
    """従量課金だけの使用順では、既存接続と保存済み会話の両方を同じIDのresumeでAPIへ移す（旧api-only相当）。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=api-key\n")
    configure_claude_payg(monkeypatch, str(api), worker="payg")
    options: list[Any] = []

    def factory(value: Any) -> QueueClaudeClient:
        options.append(value)
        return QueueClaudeClient(
            [[claude_agent_sdk.SystemMessage("init", {"session_id": "existing"}), AssistantMessage("続行")]]
        )

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        old = state.SessionState("existing", str(tmp_path), engine="claude", claude_connection=initial)
        old.claude_subscription_retry_at = 0
        old.prompt = "元の目的"
        with contextlib.closing(state.ResumePrompt("同じ文脈で続行")) as prompt:
            resumed = await backend.resume(
                "existing",
                prompt,
                str(tmp_path),
                connection_kind=initial,
                existing_session=None if cold else old,
            )
        assert resumed.session_id == "existing"
        assert resumed.claude_connection == "payg"
        assert resumed.claude_api_env_file == str(api)
        assert resumed.claude_subscription_retry_at is None
        assert options[0].resume == "existing"
        assert options[0].env["ANTHROPIC_API_KEY"] == "api-key"
        resumed.usage_limit = UsageLimitState("rejected", "five_hour", None)
        result: dict[str, Any] = {"status": "failed", "error": {"apiErrorStatus": 429}, "agent_message": ""}
        # 使用順に他の接続が無いため、API失敗でもサブスクリプションへ移らない。
        resume_waits.begin_usage_limit_wait(resumed, result)
        assert not resumed.claude_api_resume_pending
        assert resumed.claude_connection == "payg"


@pytest.mark.parametrize("record", ["server", "parent", "continued", "none"])
@pytest.mark.asyncio
async def test_handoff_path_reaches_both_backends(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, record: str) -> None:
    """生成・明示・前任からの継続の同じパスを、両backendの起動環境へ渡す。"""
    path = tmp_path / "parent-record.md"
    if record == "server":
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-state")
        monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", (None,))
        contract = tmp_path / "work-contract.md"
        contract.write_text("目的: サーバーが用意した記録先を両backendへ渡す\n", encoding="utf-8")
        prompt, path = launch_requests.delegate_request(
            "調査", whole_contract_path=str(contract), launcher_session_id="handoff-test-owner"
        )
        assert path.is_absolute() and not path.exists()
    else:
        prompt = "調査" if record == "none" else f"調査\n引き継ぎ記録先: {path}（新規）"
    if record == "continued":
        previous = state.SessionState("previous", str(tmp_path), engine="claude", prompt=prompt)
        prompt = session_handoff.compose_prompt(previous, tmp_path / "conversation.md", ())
    configure_claude_payg(monkeypatch, "")
    monkeypatch.setattr(
        claude,
        "_parent_settings",
        lambda: json.dumps({"env": {delegated_session.HANDOFF_PATH_ENV: str(tmp_path / "ancestor.md")}}),
    )
    environments: list[dict[str, str]] = []

    def factory(options: Any) -> QueueClaudeClient:
        settings = json.loads(options.settings)
        environments.append({**settings["env"], **options.env})
        return QueueClaudeClient(
            [[claude_agent_sdk.SystemMessage("init", {"session_id": "handoff"}), AssistantMessage("開始")]]
        )

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        await backend.start(prompt, str(tmp_path))

    async def capture(*_args: Any, **kwargs: Any) -> Any:
        environments.append(kwargs["env"])
        raise OSError("起動境界で停止")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    client = codex.JsonRpcProcess(_ignore_message, _ignore_message, handoff_prompt=prompt)
    with pytest.raises(codex.AppServerError, match="failed to start"):
        await client.start()
    assert len(environments) == 2
    for environment in environments:
        assert environment.get(delegated_session.HANDOFF_PATH_ENV) == (None if record == "none" else str(path))


@pytest.mark.asyncio
async def test_codex_receives_original_connection_values(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """サーバーが保持した接続値をApp Serverの起動環境へ戻す。"""
    api = tmp_path / "api.env"
    api.write_text("CUSTOM_CONNECTION=api\nAWS_PROFILE=api-profile\n")
    configure_claude_payg(monkeypatch, str(api))
    monkeypatch.setenv("AWS_PROFILE", "original-profile")
    monkeypatch.setenv("CUSTOM_CONNECTION", "original-option")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "original-oauth")
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    child_environment.prepare(os.environ)
    assert "AWS_PROFILE" not in os.environ

    async def capture(*_args: Any, **kwargs: Any) -> Any:
        assert kwargs["env"]["AWS_PROFILE"] == "original-profile"
        assert kwargs["env"]["CUSTOM_CONNECTION"] == "original-option"
        assert kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "original-oauth"
        raise OSError("起動境界で停止")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    client = codex.JsonRpcProcess(_ignore_message, _ignore_message)
    with pytest.raises(codex.AppServerError, match="failed to start"):
        await client.start()
