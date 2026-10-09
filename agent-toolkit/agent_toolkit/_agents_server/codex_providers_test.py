"""接続先の認証境界と、実backend・managerを通した有限な会話継続を検証する。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_toolkit._agents_server import codex, codex_providers, manager, session_registry
from agent_toolkit._atk import config
from agent_toolkit._common import state_paths
from agent_toolkit._testing.agents_server_support import FakeCodexClient, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")

# 公開操作の結果と、RPC・永続再開情報の内部境界を比較する。
# pylint: disable=protected-access


class ProviderClient(FakeCodexClient):
    """0.162.0の応答形式で接続先・上限・確定終端を配送する。"""

    def __init__(self) -> None:
        super().__init__()
        self.primary = "openai"
        self.account_type = "chatgpt"
        self.allowed: bool | None = True
        self.account_id = "current-account"
        self.snapshot_id: str | None = "current-account"
        self.connection = "openai"
        self.reject: set[str] = set()
        self.mismatch = False
        self.usage_error = False
        self.explicit_openai_api = False
        self.notify_closed = True
        self.lose_turn_response = False
        self.backend: codex.AppServerManager | None = None
        self.resume_started: asyncio.Event | None = None
        self.resume_release: asyncio.Event | None = None
        self.close_started: asyncio.Event | None = None
        self.close_release: asyncio.Event | None = None

    async def request(self, method: str, params: Any = None, **kwargs: Any) -> dict[str, Any]:
        if method == "config/read":
            self.requests.append((method, params))
            response: dict[str, Any] = {
                "config": {
                    "model_provider": self.primary,
                    "model_providers": {
                        "paid-a": {"env_key": "TEST_PROVIDER_KEY", "requires_openai_auth": False},
                        "paid-b": {"env_key": "TEST_PROVIDER_KEY", "requires_openai_auth": False},
                        "shared-token": {"env_key": "TEST_PROVIDER_KEY", "requires_openai_auth": True},
                        "no-auth": {"env_key": "UNSET_PROVIDER_KEY"},
                    },
                }
            }
            if self.explicit_openai_api:
                response["config"]["model_providers"]["openai"] = {
                    "requires_openai_auth": False,
                    "env_key": "TEST_PROVIDER_KEY",
                }
            return response
        if method == "account/read":
            self.requests.append((method, params))
            return {"account": {"type": self.account_type}, "workspaceRouting": {"chatgptAccountId": self.account_id}}
        if method == "account/rateLimits/read":
            self.requests.append((method, params))
            if self.usage_error:
                raise codex.AppServerError("unavailable")
            return {
                "accountId": self.snapshot_id,
                "ordinaryUsageAllowed": self.allowed,
                "rateLimits": {"primary": {"usedPercent": 100, "resetsAt": 1}},
            }
        result = await super().request(method, params, **kwargs)
        if method == "thread/unsubscribe":
            if self.close_started is not None:
                self.close_started.set()
                assert self.close_release is not None
                await self.close_release.wait()
            assert self.backend is not None
            if self.notify_closed:
                await self.backend._handle_notification({"method": "thread/closed", "params": {"threadId": params["threadId"]}})
            return {"status": "unsubscribed"}
        if method == "turn/start" and self.lose_turn_response:
            raise codex.AppServerError("turn response unavailable")
        if method == "thread/start":
            self.connection = params.get("modelProvider", self.primary)
            if self.connection in self.reject:
                raise codex.JsonRpcResponseError(method, -32600, "model is not accepted")
            result["modelProvider"] = self.connection
        if method == "thread/resume":
            if self.resume_started is not None:
                self.resume_started.set()
                assert self.resume_release is not None
                await self.resume_release.wait()
            candidate = params.get("modelProvider", self.primary)
            if candidate in self.reject:
                raise codex.JsonRpcResponseError(method, -32600, "model is not accepted")
            if not self.mismatch:
                self.connection = candidate
            result["modelProvider"] = self.connection
        return result


@pytest.fixture(name="provider_environment")
def configure_provider_environment(monkeypatch):
    """資格情報は架空のAPI値だけとし、開発機の設定を消費しない。"""
    monkeypatch.setenv(
        config._config_env_name("codex_model_providers"), "openai,missing,no-auth,shared-token,paid-a,paid-a,paid-b"
    )
    monkeypatch.setenv("TEST_PROVIDER_KEY", "isolated-fake-api-key")
    monkeypatch.delenv("UNSET_PROVIDER_KEY", raising=False)


def backend_with_client(monkeypatch, client, root=None):
    """RPC以外は実backendを使う。"""
    backend = codex.AppServerManager(root.sessions if root else {}, root._condition if root else asyncio.Condition())
    backend.client = client
    client.backend = backend

    async def ensure_client():
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    return backend


async def complete(backend, session, error=None):
    """確定したturn/completedを実通知処理へ配送する。"""
    await backend._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": session.turn_id, "status": "failed" if error else "completed", "error": error, "items": []},
            },
        }
    )


def publish_api_session(tmp_path):
    """永続再開を検証するため、確定したAPI会話を登録する。"""
    session_registry.publish(
        "provider-thread",
        terminal=True,
        engine="codex",
        cwd=str(tmp_path),
        codex_model_provider="paid-b",
        codex_subscription_provider="openai",
        status="completed",
    )


async def fail_subscription(backend, tmp_path):
    """開始済みサブスクturnの確定した上限拒否を配送する。"""
    session = await backend.start("元の作業", str(tmp_path), None, None)
    await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary", "account", "allowed", "expected"),
    [
        ("openai", "chatgpt", True, "openai"),
        ("openai", "chatgpt", False, "paid-a"),
        ("openai", "chatgpt", None, "openai"),
        ("paid-b", "chatgpt", False, "paid-b"),
        ("openai", "apiKey", False, "openai"),
    ],
)
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
async def test_provider_environment_start(
    monkeypatch, tmp_path, provider_environment, primary, account, allowed, expected, launch_kind
):
    del provider_environment
    client = ProviderClient()
    client.primary, client.account_type, client.allowed = primary, account, allowed
    monkeypatch.setenv(
        config._config_env_name("codex_model_providers"), f"{primary},missing,no-auth,shared-token,paid-a,paid-b"
    )
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("元の作業", str(tmp_path), None, None, launch_kind=launch_kind)
        assert session.codex_model_provider == expected
        assert client.connection == expected
        assert all(params["cwd"] == str(tmp_path) for method, params in client.requests if method == "config/read")
        if primary != "openai" or account == "apiKey":
            assert not any(method == "account/rateLimits/read" for method, _ in client.requests)
        if primary == "openai" and account == "chatgpt" and allowed is False:
            assert not any(method == "thread/resume" for method, _ in client.requests)
            assert [params["modelProvider"] for method, params in client.requests if method == "thread/start"] == ["paid-a"]
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", [True, False])
async def test_manager_turn_start_failure_finishes_without_provider_details(
    monkeypatch, tmp_path, provider_environment, subscription
):
    """RPC拒否を実監視から継続し、各API候補を一度だけ試して安全な終端を回収する。"""
    del provider_environment
    root = manager.AgentsServerManager()
    client = ProviderClient()
    client.allowed = subscription
    backend = backend_with_client(monkeypatch, client, root)
    install_backend(root, "codex", backend)
    original_request = client.request
    attempted_turns = []

    async def request(method, params=None, **kwargs):
        if method == "turn/start":
            attempted_turns.append(client.connection)
            info = "usageLimitExceeded" if client.connection == "openai" else {"httpConnectionFailed": {"httpStatusCode": 401}}
            raise codex.JsonRpcResponseError(
                method,
                -32600,
                f"provider {client.connection} rejected the request",
                {"codexErrorInfo": info, "modelProvider": client.connection, "providerDetails": {"token": "private-value"}},
            )
        return await original_request(method, params, **kwargs)

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    root.activate()
    try:
        started = await asyncio.wait_for(root.start("codex:gpt-6-sol/high", "元の作業", str(tmp_path)), 5)
        root._wait_timeouts["main"] = 3
        result = await asyncio.wait_for(root.wait(), 5)
        assert result["status"] == "failed"
        assert attempted_turns == (["openai", "paid-a", "paid-b"] if subscription else ["paid-a", "paid-b"])
        session = root.sessions["thread-codex"]
        assert not session.codex_provider_resume_pending
        assert not session.awaiting_auto_resume and session.pending_result is None
        public = json.dumps([started, result, root.list_sessions(), root.show_session(session.session_id)])
        assert "modelProvider" not in public and "providerDetails" not in public
        assert "private-value" not in public and "paid-a" not in public and "paid-b" not in public
        assert all(params["threadId"] == session.session_id for method, params in client.requests if method == "thread/resume")
    finally:
        await root.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allowed", "snapshot", "failure", "expected"),
    [
        (False, "current-account", False, False),
        (True, "current-account", False, True),
        (None, "current-account", False, None),
        (True, "old-account", False, None),
        (True, "current-account", True, None),
    ],
)
async def test_provider_recovery_current_account(tmp_path, provider_environment, allowed, snapshot, failure, expected):
    del provider_environment
    client = ProviderClient()
    client.allowed, client.snapshot_id, client.usage_error = allowed, snapshot, failure
    selection = await codex_providers.select(client.request, str(tmp_path))
    assert selection.ordinary_usage_allowed is expected
    assert selection.candidates == ("paid-a", "paid-b")


@pytest.mark.asyncio
async def test_explicit_openai_api_auth_is_preserved(tmp_path, provider_environment):
    del provider_environment
    client = ProviderClient()
    client.explicit_openai_api, client.allowed = True, False
    selection = await codex_providers.select(client.request, str(tmp_path))
    assert not selection.subscription
    assert not any(method == "account/rateLimits/read" for method, _ in client.requests)


@pytest.mark.asyncio
async def test_provider_failover_sequence(monkeypatch, tmp_path, provider_environment):
    del provider_environment
    client = ProviderClient()
    client.reject.add("paid-a")
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await fail_subscription(backend, tmp_path)
        assert session.status == "running" and session.pending_result is not None
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert response["delivery"] == "reply_started"
        resumes = [params["modelProvider"] for method, params in client.requests if method == "thread/resume"]
        assert resumes == ["paid-a", "paid-b"]
        assert session.codex_model_provider == "paid-b"
        assert all(params["threadId"] == session.session_id for method, params in client.requests if method == "thread/resume")
        await complete(backend, session, {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}})
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert response["delivery"] == "reply_failed"
        assert session.status == "failed"
        assert [params["modelProvider"] for method, params in client.requests if method == "thread/resume"] == resumes
        turns = [params["input"] for method, params in client.requests if method == "turn/start"]
        assert len(turns) == 2
        assert "元の作業" not in json.dumps(turns[1], ensure_ascii=False)
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["rateLimitExceeded", "serverOverloaded", "unauthorized"])
async def test_subscription_non_usage_failure_does_not_switch(monkeypatch, tmp_path, provider_environment, error):
    del provider_environment
    backend = backend_with_client(monkeypatch, ProviderClient())
    try:
        session = await backend.start("元の作業", str(tmp_path), None, None)
        await complete(backend, session, {"codexErrorInfo": error})
        assert not session.codex_provider_resume_pending
        assert session.codex_model_provider == "openai"
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider_mismatch", "missing_closure", "ambiguous_turn"])
async def test_resume_confirmation_mismatch_prevents_turn(monkeypatch, tmp_path, provider_environment, failure):
    del provider_environment
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await fail_subscription(backend, tmp_path)
        client.mismatch = failure == "provider_mismatch"
        client.notify_closed = failure != "missing_closure"
        client.lose_turn_response = failure == "ambiguous_turn"
        monkeypatch.setattr(codex, "DEFAULT_WAIT_TIMEOUT", 0.01)
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        ambiguous = failure == "ambiguous_turn"
        assert response["delivery"] == ("reply_ambiguous" if ambiguous else "reply_failed")
        turns = len([1 for method, _ in client.requests if method == "turn/start"])
        assert turns == (2 if ambiguous else 1)
        assert session.codex_model_provider == ("paid-a" if ambiguous else "openai")
        if ambiguous:
            assert session.turn_start_ambiguous
            assert not await backend._switch_provider(session, client)
            assert len([1 for method, _ in client.requests if method == "turn/start"]) == turns
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_interrupt_during_provider_resume_prevents_turn(monkeypatch, tmp_path, provider_environment):
    del provider_environment
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await fail_subscription(backend, tmp_path)
        client.resume_started, client.resume_release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(backend.send_message(session, codex_providers.CONTINUE_PROMPT))
        try:
            await asyncio.wait_for(client.resume_started.wait(), 1)
            session.interrupt_requested = True
            await backend.interrupt(session)
        finally:
            client.resume_release.set()
            await asyncio.wait_for(task, 1)
        assert len([1 for method, _ in client.requests if method == "turn/start"]) == 1
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_provider_registry_lifecycle_and_public_projection(monkeypatch, tmp_path, provider_environment):
    del provider_environment
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    publish_api_session(tmp_path)
    for release in (None, "retention_expired", "stopped"):
        if release:
            session_registry.release("provider-thread", reason=release)
        root = manager.AgentsServerManager()
        try:
            restored = root._restore_registry_session("provider-thread")
            assert restored is not None
            assert restored.codex_model_provider == "paid-b"
            assert restored.codex_subscription_provider == "openai"
            public = json.dumps(root.show_session("provider-thread"), ensure_ascii=False)
            assert "paid-b" not in public and "codex_model_provider" not in public
        finally:
            await root.close()


@pytest.mark.asyncio
async def test_provider_failover_result_collection(monkeypatch, tmp_path, provider_environment, caplog):
    """公開startとwaitが内部失敗を返さず、監視後の最終結果だけを回収する。"""
    del provider_environment
    root = manager.AgentsServerManager()
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client, root)
    install_backend(root, "codex", backend)
    pending = []
    original_request = client.request

    async def finish_turn():
        await asyncio.sleep(0)
        session = backend.sessions["thread-codex"]
        if client.connection == "openai":
            await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        else:
            await backend._handle_notification(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": session.session_id,
                        "turnId": session.turn_id,
                        "item": {"type": "agentMessage", "id": "answer", "text": "継続後の結果"},
                    },
                }
            )
            await complete(backend, session)

    async def request(method, params=None, **kwargs):
        response = await original_request(method, params, **kwargs)
        if method == "turn/start":
            pending.append(asyncio.create_task(finish_turn()))
        return response

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    root.activate()
    try:
        started = await asyncio.wait_for(root.start("codex:gpt-6-sol/high", "元の作業", str(tmp_path)), 5)
        root._wait_timeouts["main"] = 3
        result = await asyncio.wait_for(root.wait(), 5)
        assert result["status"] == "completed"
        assert result["agent_message"] == "継続後の結果"
        public = json.dumps([started, result, root.list_sessions(), root.show_session("thread-codex")])
        assert "paid-a" not in public and "codex_model_provider" not in public
        assert "isolated-fake-api-key" not in caplog.text
        assert "provider=paid-a" in caplog.text
        assert len([1 for method, _ in client.requests if method == "thread/start"]) == 1
    finally:
        await root.close()
        await asyncio.gather(*pending)


@pytest.mark.asyncio
async def test_restored_api_provider_ignores_changed_configuration(monkeypatch, tmp_path, provider_environment):
    """stop相当の解放と再起動後も公開追送が確定済み接続先を使う。"""
    del provider_environment
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    publish_api_session(tmp_path)
    session_registry.release("provider-thread", reason="stopped")
    monkeypatch.setenv(config._config_env_name("codex_model_providers"), "")
    root = manager.AgentsServerManager()
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client, root)
    install_backend(root, "codex", backend)
    try:
        response = await root.send_message("provider-thread", "未完了の続き")
        assert response["delivery"] == "reply_started"
        assert client.connection == "paid-b"
        assert all(params["modelProvider"] == "paid-b" for method, params in client.requests if method == "thread/resume")
        assert not any(method == "account/rateLimits/read" for method, _ in client.requests)
        assert "paid-b" not in json.dumps(response)
    finally:
        await root.close()


@pytest.mark.asyncio
async def test_provider_transition_preserves_pending_children(monkeypatch, tmp_path, provider_environment):
    """上限失敗が孫の保留を解除せず、移行後の次turnも追跡を維持する。"""
    del provider_environment
    backend = backend_with_client(monkeypatch, ProviderClient())
    try:
        session = await backend.start("孫の結果を待つ作業", str(tmp_path))
        session.live_child_session_ids.add("child-thread")
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        assert session.pending_result is not None and session.auto_resume_deadline is not None
        assert not session.codex_provider_resume_pending
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert response["delivery"] == "reply_started"
        assert session.live_child_session_ids == {"child-thread"}
        assert session.codex_model_provider == "paid-a"
        assert not session.codex_provider_resume_pending
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_cancel_before_api_resume_keeps_provider(monkeypatch, tmp_path):
    """再開の起動前にkillしても、次の追送へAPI接続先を保持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    publish_api_session(tmp_path)
    root = manager.AgentsServerManager()
    try:
        restored = root._restore_registry_session("provider-thread")
        assert restored is not None
        pending, _ = root._start_resume(restored, "未完了の続き", None)
        session, accepted = await root._cancel_pending_resume(pending, 1)
        assert not accepted and session.status == "interrupted"
        assert session.codex_model_provider == "paid-b"
        assert session.codex_subscription_provider == "openai"
    finally:
        await root.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_response", [True, False])
@pytest.mark.parametrize("resume_provider_response", [True, False])
async def test_empty_provider_configuration_keeps_current_resume_settings(
    monkeypatch, tmp_path, provider_response, resume_provider_response
):
    """移行設定が空なら応答の接続先を固定せず、再開時の通常設定を使う。"""
    monkeypatch.setenv(config._config_env_name("codex_model_providers"), "")
    client = ProviderClient()
    original_request = client.request

    async def request(method, params=None, **kwargs):
        response = await original_request(method, params, **kwargs)
        if (method == "thread/start" and not provider_response) or (method == "thread/resume" and not resume_provider_response):
            response.pop("modelProvider", None)
        return response

    monkeypatch.setattr(client, "request", request)
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("通常の作業", str(tmp_path))
        assert session.codex_model_provider is None
        assert session.codex_subscription_provider is None
        await complete(backend, session)
        client.primary = "paid-b"
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started"
        assert client.connection == "paid-b"
        assert all("modelProvider" not in params for method, params in client.requests if method == "thread/resume")
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", [True, False])
async def test_turn_start_failure_keeps_provider_details_private(monkeypatch, tmp_path, provider_environment, subscription):
    """構造化失敗で有限継続を判定し、追加の接続先情報は公開結果へ入れない。"""
    del provider_environment
    client = ProviderClient()
    client.allowed = subscription
    original_request = client.request

    async def request(method, params=None, **kwargs):
        if method == "turn/start":
            info = "usageLimitExceeded" if client.connection == "openai" else {"httpConnectionFailed": {"httpStatusCode": 401}}
            raise codex.JsonRpcResponseError(
                method,
                -32600,
                f"provider {client.connection} rejected the request",
                {"codexErrorInfo": info, "modelProvider": client.connection, "providerDetails": {"token": "private-value"}},
            )
        return await original_request(method, params, **kwargs)

    monkeypatch.setattr(client, "request", request)
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("元の作業", str(tmp_path))
        assert session.codex_provider_resume_pending
        for _ in range(3):
            if session.status == "failed":
                break
            await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert session.status == "failed"
        assert {"paid-a", "paid-b"} <= session.codex_attempted_provider_ids
        public = json.dumps([session.public_status(), session.previous_result()])
        assert "modelProvider" not in public and "providerDetails" not in public
        assert "private-value" not in public and "paid-a" not in public and "paid-b" not in public
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_provider_order_overrides_codex_default(monkeypatch, tmp_path, provider_environment):
    """Codexの通常接続がAPIでも明示列の先頭でサブスクを開始する。"""
    del provider_environment
    client = ProviderClient()
    client.primary = "paid-b"
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("優先順の作業", str(tmp_path))
        assert client.connection == "openai"
        assert session.codex_subscription_provider == "openai"
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started"
        assert client.connection == "paid-a"
        assert session.session_id == "thread-codex"
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("next_order", ["paid-a,openai,paid-b", "paid-b,paid-a"])
async def test_api_primary_uses_ordered_api_candidates(monkeypatch, tmp_path, provider_environment, next_order):
    """API先頭の確定失敗は後続APIへ進み、ChatGPTへ戻らない。"""
    del provider_environment
    monkeypatch.setenv(config._config_env_name("codex_model_providers"), "paid-a,openai,paid-b")
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("API優先の作業", str(tmp_path))
        assert client.connection == "paid-a" and session.codex_subscription_provider is None
        assert not any(method == "account/rateLimits/read" for method, _ in client.requests)
        monkeypatch.setenv(config._config_env_name("codex_model_providers"), next_order)
        await complete(backend, session, {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}})
        assert session.codex_provider_resume_pending
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started" and client.connection == "paid-b"
        assert [params["modelProvider"] for method, params in client.requests if method == "thread/resume"] == ["paid-b"]
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["paid-a", "missing,paid-a,paid-b"])
async def test_api_primary_start_rejection_is_finite(monkeypatch, tmp_path, provider_environment, order):
    """無効候補と開始拒否から指定順だけを試し、通常接続へ戻らない。"""
    del provider_environment
    monkeypatch.setenv(config._config_env_name("codex_model_providers"), order)
    client = ProviderClient()
    client.reject.add("paid-a")
    backend = backend_with_client(monkeypatch, client)
    try:
        if order == "paid-a":
            with pytest.raises(codex.AppServerError, match="candidates were rejected"):
                await backend.start("拒否するAPI", str(tmp_path))
        else:
            session = await backend.start("後続のAPI", str(tmp_path))
            assert session.codex_model_provider == "paid-b"
        assert [params["modelProvider"] for method, params in client.requests if method == "thread/start"] == (
            ["paid-a"] if order == "paid-a" else ["paid-a", "paid-b"]
        )
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("old", ["", "paid-a,paid-b"])
async def test_legacy_provider_setting_migrates_once(monkeypatch, tmp_path, old):
    """保存旧列を実効主接続先で移し、明示空と無関係な設定を保つ。"""
    monkeypatch.setenv("TEST_PROVIDER_KEY", "isolated-fake-api-key")
    monkeypatch.delenv(config._config_env_name("codex_model_providers"), raising=False)
    monkeypatch.delenv(config._config_env_name("codex_fallback_model_providers"), raising=False)
    config._save_config({"codex_fallback_model_providers": old, "codex_fast_mode": "true"})
    client = ProviderClient()
    client.primary = "paid-b"
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("旧設定の移行", str(tmp_path))
        stored = config._load_config()
        assert stored == {"codex_model_providers": "paid-b,paid-a" if old else "", "codex_fast_mode": "true"}
        assert session.codex_model_provider == ("paid-b" if old else None)
        client.primary = "openai"
        assert config.migrate_codex_provider_setting("openai") == (("paid-b", "paid-a") if old else ())
        assert config._load_config() == stored
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_legacy_environment_does_not_override_new_empty(monkeypatch, tmp_path):
    """新しい明示空は旧envを無効にし、旧env単独の移行は保存しない。"""
    monkeypatch.delenv(config._config_env_name("codex_model_providers"), raising=False)
    monkeypatch.setenv(config._config_env_name("codex_fallback_model_providers"), "paid-a")
    monkeypatch.setenv("TEST_PROVIDER_KEY", "isolated-fake-api-key")
    config._save_config({"codex_model_providers": ""})
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client)
    try:
        session = await backend.start("新設定の空", str(tmp_path))
        assert session.codex_model_provider is None
        config._save_config({})
        selection = await codex_providers.select(client.request, str(tmp_path))
        assert selection.primary == "openai" and selection.candidates == ("paid-a",)
        assert config._load_config() == {}
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stopped", "retention_expired"])
async def test_api_primary_registry_restores_after_release(monkeypatch, tmp_path, reason):
    """サブスク履歴のない主APIも解放後の公開追送で同じ接続先を維持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        "provider-thread", terminal=True, engine="codex", cwd=str(tmp_path), codex_model_provider="paid-b", status="completed"
    )
    session_registry.release("provider-thread", reason=reason)
    root = manager.AgentsServerManager()
    client = ProviderClient()
    backend = backend_with_client(monkeypatch, client, root)
    install_backend(root, "codex", backend)
    try:
        response = await root.send_message("provider-thread", "APIの続き")
        assert response["delivery"] == "reply_started"
        assert client.connection == "paid-b"
        assert root.sessions["provider-thread"].codex_subscription_provider is None
    finally:
        await root.close()


@pytest.mark.asyncio
async def test_legacy_provider_migration_save_failure_keeps_original(monkeypatch, tmp_path):
    """移行の保存失敗で旧設定を失わず、後で再実行できる。"""
    config._save_config({"codex_fallback_model_providers": "paid-a", "codex_fast_mode": "true"})
    before = config._config_file_path().read_text()

    def fail_write(*_args, **_kwargs):
        raise OSError("migration storage unavailable")

    monkeypatch.setattr(config, "atomic_write", fail_write)
    with pytest.raises(OSError, match="migration storage unavailable"):
        await codex_providers.select(ProviderClient().request, str(tmp_path))
    assert config._config_file_path().read_text() == before
