"""Codex接続先のresumeを検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from agent_toolkit._agents_server import manager, record_paths, session_handoff, state
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import install_backend
from agent_toolkit._testing.codex_provider_support import (
    ProviderClient,
    backend_with_client,
    clear_connections,
    complete,
    publish_api_session,
    set_connections,
    write_resume_rollout,
)

# 公開結果とRPCの内部状態を比較する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_provider_registry_lifecycle_and_public_projection(monkeypatch, tmp_path, provider_environment):
    del provider_environment
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    publish_api_session(tmp_path)
    for release in (None, "retention_expired", "stopped"):
        if release:
            session_registry.release("provider-thread", reason=release)
        async with manager.AgentsServerManager() as root:
            restored = await root._restore_registry_session("provider-thread")
            assert restored is not None
            assert restored.codex_model_provider == "paid-b"
            assert restored.codex_subscription_provider == "openai"
            public = json.dumps((await root.show_session("provider-thread")), ensure_ascii=False)
            assert "paid-b" not in public and "codex_model_provider" not in public


@pytest.mark.asyncio
async def test_restored_api_provider_ignores_changed_configuration(monkeypatch, tmp_path, provider_environment):
    """stop相当の解放と再起動の後も、公開の追送は確定済みの接続先を使う。"""
    del provider_environment
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    publish_api_session(tmp_path)
    session_registry.release("provider-thread", reason="stopped")
    clear_connections(monkeypatch)
    async with manager.AgentsServerManager() as root:
        client = ProviderClient()
        async with backend_with_client(monkeypatch, client, root) as backend:
            install_backend(root, "codex", backend)
            try:
                response = await root.send_message("provider-thread", "未完了の続き")
                assert response["delivery"] == "reply_started"
                assert client.connection == "paid-b"
                assert all(
                    params["modelProvider"] == "paid-b" for method, params in client.requests if method == "thread/resume"
                )
                assert not any(method == "account/rateLimits/read" for method, _ in client.requests)
                assert "paid-b" not in json.dumps(response)
            finally:
                await root.close()


@pytest.mark.asyncio
async def test_cancel_before_api_resume_keeps_provider(monkeypatch, tmp_path):
    """再開の起動前にkillしても、次の追送へAPI接続先を保持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    publish_api_session(tmp_path)
    async with manager.AgentsServerManager() as root:
        restored = await root._restore_registry_session("provider-thread")
        assert restored is not None
        pending, _ = root._start_resume(restored, "未完了の続き", None)
        session, accepted = await root._cancel_pending_resume(pending, 1)
        assert not accepted and session.status == "interrupted"
        assert session.codex_model_provider == "paid-b"
        assert session.codex_subscription_provider == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stopped", "retention_expired"])
async def test_api_primary_registry_restores_after_release(monkeypatch, tmp_path, reason):
    """サブスクリプションの利用履歴が無い主APIの接続先も、解放後の公開の追送で同じ接続先を維持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        "provider-thread",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="codex",
        cwd=str(tmp_path),
        codex_model_provider="paid-b",
    )
    session_registry.release("provider-thread", reason=reason)
    async with manager.AgentsServerManager() as root:
        client = ProviderClient()
        async with backend_with_client(monkeypatch, client, root) as backend:
            install_backend(root, "codex", backend)
            try:
                response = await root.send_message("provider-thread", "APIの続き")
                assert response["delivery"] == "reply_started"
                assert client.connection == "paid-b"
                assert root.sessions["provider-thread"].codex_subscription_provider is None
            finally:
                await root.close()


@pytest.mark.parametrize(
    "item",
    [
        {"type": "compaction", "id": "cmp_fake", "encrypted_content": "fake-compressed-state"},
        {"type": "compacted", "payload": {"compaction_response_id": "cmp_fake"}},
        {"type": "reasoning", "summary": [], "encrypted_content": "fake-reasoning-state"},
    ],
)
@pytest.mark.parametrize("primary", ["openai", "paid-a"])
@pytest.mark.asyncio
async def test_opaque_state_switch_collects_result_and_hands_off(monkeypatch, tmp_path, provider_environment, item, primary):
    """公開の追送・結果回収・引き継ぎ起動を経て、元会話と進捗を保持した後任を起動する。"""
    del provider_environment
    # API主接続は従量課金に置き、残高不足の並べ替えが後任の接続を変えないようにする。
    if primary == "openai":
        set_connections(monkeypatch, "subscription,payg")
    else:
        set_connections(monkeypatch, "payg,credits", credits_provider="paid-b", payg="paid-a")
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    record = tmp_path / "rollout.jsonl"
    client = ProviderClient()
    client.primary = primary
    client.rollout_path = record
    original_request = client.request
    starts = 0

    async def request(method, params=None, **kwargs):
        nonlocal starts
        response = await original_request(method, params, **kwargs)
        if method == "thread/start":
            starts += 1
            if starts > 1:
                response["thread"]["id"] = "successor-thread"
        if method == "turn/start" and client.connection != primary:
            pytest.fail("互換性の無い接続先が元threadの状態を消費した")
        return response

    def extract(command, **_kwargs):
        work = pathlib.Path(command[command.index("--work-dir") + 1])
        (work / "conversation.md").write_text("完了済みの編集と残る検証の会話", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(record_paths, "find_session_record", lambda _identifier: record_paths.SessionRecord("codex", (record,)))
    monkeypatch.setattr(session_handoff.process_tree, "run_with_timeout", extract)
    async with manager.AgentsServerManager() as root, backend_with_client(monkeypatch, client, root) as backend:
        install_backend(root, "codex", backend)
        started = await root.start("codex:gpt-6-sol/high", "編集してから検証する", str(tmp_path))
        session = root.sessions[started["session_id"]]
        await complete(backend, session)
        await root.send_message(session.session_id, "検証は最後にまとめる")
        session.agent_message = "編集を完了し、検証が残っている"
        failure = (
            {"codexErrorInfo": "usageLimitExceeded"}
            if primary == "openai"
            else {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}
        )
        original = write_resume_rollout(record, session.session_id, primary, item)
        await complete(backend, session, failure)
        before = sum(method == "turn/start" for method, _params in client.requests)
        response = await root.send_message(session.session_id, "残る作業を続ける")
        assert response["delivery"] == "reply_failed"
        result = await root.wait()
        assert result["status"] == "failed" and "handoff_from" in result["next_action"]
        assert result["agent_message"] == "編集を完了し、検証が残っている"
        assert sum(method == "turn/start" for method, _params in client.requests) == before
        assert not any(method == "thread/resume" and params["modelProvider"] != primary for method, params in client.requests)
        assert session.codex_model_provider == primary
        assert record.read_bytes() == original
        successor = await root.start_handoff(session.session_id)
        prompt = root.sessions[successor["session_id"]].prompt
        assert "検証は最後にまとめる" in prompt and "編集を完了し、検証が残っている" in prompt
        assert "完了済みの操作" in prompt and str(record) in prompt
        public = json.dumps([response, result, successor], ensure_ascii=False)
        assert "codex_state_provider" not in public and "paid-b" not in public


@pytest.mark.parametrize("lifecycle", ["retained", "released", "restarted", "settings", "legacy_migration"])
@pytest.mark.asyncio
async def test_opaque_state_resume_uses_origin_in_every_lifecycle(monkeypatch, tmp_path, provider_environment, lifecycle):
    """保持・解放・復元した会話の生成元と選択先が異なる場合、次turnより前に再開を拒否する。"""
    del provider_environment
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    client = ProviderClient()
    record = tmp_path / "rollout.jsonl"
    client.rollout_path = record
    original = write_resume_rollout(
        record, "provider-thread", "openai", {"type": "reasoning", "encrypted_content": "fake-state"}
    )
    session_registry.publish(
        "provider-thread",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="codex",
        cwd=str(tmp_path),
        model="gpt-6-sol",
        model_type="codex:gpt-6-sol/high",
        effort="high",
        turn_seq=1,
        codex_model_provider=None if lifecycle == "settings" else "paid-b",
        codex_state_provider=None if lifecycle == "legacy_migration" else "openai",
        launch_info=session_registry.LaunchInfo(
            prompt="元の進捗を保つ", followup_prompts=("最後に検証する",), final_responses=((1, "編集済み"),)
        ),
    )
    if lifecycle == "released":
        session_registry.release("provider-thread", reason="stopped")
    if lifecycle == "settings":
        client.primary = "paid-b"
        clear_connections(monkeypatch)
    async with manager.AgentsServerManager() as root, backend_with_client(monkeypatch, client, root) as backend:
        install_backend(root, "codex", backend)
        if lifecycle == "retained":
            await root.show_session("provider-thread")
        response = await root.send_message("provider-thread", "残る工程")
        assert response["delivery"] == "reply_failed"
        result = await root.wait()
        assert "handoff_from" in result["next_action"]
        restored = root.sessions["provider-thread"]
        assert dict(restored.final_responses)[1] == "編集済み"
        assert restored.followup_prompts == ("最後に検証する",)
        assert not any(method in {"thread/resume", "turn/start"} for method, _params in client.requests)
        assert record.read_bytes() == original


@pytest.mark.asyncio
async def test_opaque_state_created_after_safe_switch_resumes_on_selected_connection(
    monkeypatch, tmp_path, provider_environment
):
    """状態なしの移行後に生成した暗号化内容は、保存・解放後も同じ生成先で再利用できる。"""
    del provider_environment
    client = ProviderClient()
    record = tmp_path / "rollout.jsonl"
    client.rollout_path = record
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("続ける作業", str(tmp_path))
        write_resume_rollout(record, session.session_id, "openai", None)
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        assert (await backend.send_message(session, "継続"))["delivery"] == "reply_started"
        assert session.codex_state_provider == "paid-a"
        await complete(backend, session)
        original = write_resume_rollout(
            record, session.session_id, "openai", {"type": "reasoning", "encrypted_content": "fake-api-state"}
        )
        info = state.SessionResumeState.from_session(session)
        await backend.release_session(session)
        client.closed = False
        resumed = await backend.resume(
            session.session_id,
            state.ResumePrompt("同じ接続で継続"),
            str(tmp_path),
            model_provider=info.codex_model_provider,
            state_provider=info.codex_state_provider,
        )
        assert resumed.status == "running" and client.connection == "paid-a"
        assert record.read_bytes() == original


@pytest.mark.parametrize("record_kind", ["missing", "broken", "no_metadata", "unknown_source", "same_source"])
@pytest.mark.asyncio
async def test_legacy_resume_requires_observed_state_compatibility(monkeypatch, tmp_path, record_kind):
    """記録欠損・未知生成元と、同一接続で再開できる旧形式の暗号化会話を区別する。"""
    clear_connections(monkeypatch)
    client = ProviderClient()
    record = tmp_path / "rollout.jsonl"
    client.rollout_path = record
    opaque = {"type": "reasoning", "encrypted_content": "fake-state"}
    if record_kind == "broken":
        record.write_text('{"payload":', encoding="utf-8")
    elif record_kind == "no_metadata":
        record.write_text(json.dumps({"type": "response_item", "payload": opaque}) + "\n", encoding="utf-8")
    elif record_kind != "missing":
        write_resume_rollout(record, "thread-codex", "openai" if record_kind == "same_source" else None, opaque)
    async with backend_with_client(monkeypatch, client) as backend:
        resumed = await backend.resume("thread-codex", state.ResumePrompt("旧記録の継続"), str(tmp_path))
        expected = record_kind == "same_source"
        assert resumed.status == ("running" if expected else "failed")
        assert sum(method == "turn/start" for method, _params in client.requests) == int(expected)
        if not expected:
            action = session_handoff.result_next_action(
                {"engine": resumed.engine, "session_id": resumed.session_id, **resumed.previous_result()}
            )
            assert action is not None and "handoff_from" in action


@pytest.mark.asyncio
async def test_verified_state_provider_survives_registry_release_and_restart(monkeypatch, tmp_path):
    """再開を許可した生成先の記録が、通常設定の会話でも解放・再起動を越える。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        "verified-thread",
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="codex",
        cwd=str(tmp_path),
        codex_state_provider="paid-b",
    )
    session_registry.release("verified-thread", reason="stopped")
    async with manager.AgentsServerManager() as root:
        restored = await root._restore_registry_session("verified-thread")
        assert restored is not None and restored.codex_state_provider == "paid-b"
        assert restored.codex_model_provider is None
        assert "codex_state_provider" not in json.dumps(await root.show_session("verified-thread"))
