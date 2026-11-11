"""Codex接続先のusageを検証する。"""

from __future__ import annotations

import json

import pytest

from agent_toolkit._agents_server import manager, state, turn_state
from agent_toolkit._atk import config
from agent_toolkit._testing.codex_provider_support import (
    ProviderClient,
    backend_with_client,
    complete,
    set_connections,
    write_resume_rollout,
)

# 公開結果とRPCの内部状態を比較する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    ("policy", "minutes", "waits"),
    [
        ("never", 300, False),
        ("five-hour", 300, True),
        ("five-hour", 10080, False),
        ("five-hour", None, False),
        ("always", 10080, True),
        ("always", None, True),
    ],
)
@pytest.mark.asyncio
async def test_codex_usage_wait_policy_selects_same_session_or_api(
    monkeypatch, tmp_path, provider_environment, policy, minutes, waits
):
    """保存記録の実際の窓長と設定から、同一会話の解除待ちとAPI候補への継続を選ぶ。"""
    del provider_environment
    monkeypatch.setenv(config._config_env_name("codex_usage_limit_wait"), policy)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    directory = tmp_path / "sessions" / "2026" / "10" / "23"
    directory.mkdir(parents=True)
    client = ProviderClient()
    rollout = directory / "rollout-test-thread-codex.jsonl"
    client.rollout_path = rollout
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("上限後も残工程を継続する", str(tmp_path))
        write_resume_rollout(rollout, session.session_id, "openai", None)
        with rollout.open("a", encoding="utf-8") as stream:
            for payload in (
                {
                    "type": "token_count",
                    "rate_limits": {"primary": {"used_percent": 100, "window_minutes": minutes, "resets_at": 2000000000}},
                },
                {"type": "task_complete", "error": {"codex_error_info": "usage_limit_exceeded"}},
            ):
                stream.write(json.dumps({"type": "event_msg", "payload": payload}) + "\n")
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        assert (session.usage_limit_resume_at is not None) is waits
        assert session.codex_provider_resume_pending is not waits
        assert (await backend.send_message(session, "残る工程を続ける"))["delivery"] == "reply_started"
        assert client.connection == ("openai" if waits else "paid-a")


@pytest.mark.parametrize(
    ("policy", "minutes", "expected"),
    [
        ("never", 300, "paid-a"),
        ("five-hour", 300, "openai"),
        ("five-hour", 10080, "paid-a"),
        ("always", None, "openai"),
    ],
)
@pytest.mark.asyncio
async def test_known_subscription_exhaustion_obeys_wait_policy_before_start(
    monkeypatch, tmp_path, provider_environment, policy, minutes, expected
):
    """起動前の枯渇報告も、通知後の待機と同じ利用枠の種類で接続候補を選ぶ。"""
    del provider_environment
    monkeypatch.setenv(config._config_env_name("codex_usage_limit_wait"), policy)
    client = ProviderClient()
    client.allowed = False
    client.window_minutes = minutes
    async with backend_with_client(monkeypatch, client) as backend:
        await backend.start("上限時の起動", str(tmp_path))
        assert client.connection == expected


@pytest.mark.asyncio
async def test_orchestrator_role_uses_its_order_and_does_not_switch_on_failure(monkeypatch, tmp_path, provider_environment):
    """統括担当は統括担当の使用順で起動し、利用上限などの失敗では会話の中で次の候補へ切り替えない。"""
    del provider_environment
    set_connections(monkeypatch, "credits,subscription")
    monkeypatch.setenv(config._config_env_name("codex_orchestrator_connections"), "subscription,credits")
    monkeypatch.setenv(config._config_env_name("codex_usage_limit_wait"), "never")
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("統括の作業", str(tmp_path), model_type="orchestrate")
        assert session.codex_model_provider == "openai"
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        assert not session.codex_provider_resume_pending
        assert session.status == "failed"


@pytest.mark.asyncio
async def test_provider_recovery_delivery_failure_publishes_original_result(monkeypatch, tmp_path):
    """接続切替前の配送失敗は、理由を保留し続けず元の失敗結果を回収可能にする。"""

    async def unavailable(_session, _prompt, **_kwargs):
        raise OSError("資源案内を読み取れない")

    async with manager.AgentsServerManager(None) as owner:
        session = state.SessionState("provider-delivery", str(tmp_path), engine="codex", announced=True)
        original = {"status": "failed", "agent_message": "元の失敗", "error": {"message": "connection rejected"}}
        session.hold_result(turn_state.CodexProviderWait(original))
        owner.sessions[session.session_id] = session
        monkeypatch.setattr(owner, "_send_with_resources", unavailable)
        await owner._advance_child_session_wait(session)
        assert session.result_available and not session.codex_provider_resume_pending
        result = await owner.wait()
        assert result["status"] == "failed" and result["agent_message"] == original["agent_message"]
