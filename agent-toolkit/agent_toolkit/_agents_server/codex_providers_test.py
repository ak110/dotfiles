"""接続先の認証境界と、実backend・managerを通した有限な会話継続を検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    api_credits,
    codex,
    codex_providers,
    manager,
    record_paths,
    resume_waits,
    session_handoff,
    state,
)
from agent_toolkit._atk import codex_connection, config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeCodexClient, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")

# 公開操作の結果に加え、RPCと永続再開情報の内部状態も直接比較する。
# pylint: disable=protected-access


class ProviderClient(FakeCodexClient):
    """0.162.0の応答形式で接続先・上限・確定終了を配送する。"""

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
        self.window_minutes: int | None = None
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
                "rateLimits": {"primary": {"usedPercent": 100, "resetsAt": 1, "windowDurationMins": self.window_minutes}},
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


_CODEX_CONNECTION_KEYS = (
    "codex_credits_provider",
    "codex_payg_provider",
    "codex_orchestrator_connections",
    "codex_worker_connections",
)


def set_connections(monkeypatch, order: str, *, credits_provider: str = "paid-a", payg: str = "paid-b") -> None:
    """Codexの接続設定を、クレジット・従量課金のproviderと委譲先の使用順で与える。"""
    monkeypatch.setenv(config._config_env_name("codex_credits_provider"), credits_provider)
    monkeypatch.setenv(config._config_env_name("codex_payg_provider"), payg)
    monkeypatch.setenv(config._config_env_name("codex_worker_connections"), order)


def clear_connections(monkeypatch) -> None:
    """Codexの接続設定を全て外し、config.tomlの通常設定へ戻す。"""
    for key in _CODEX_CONNECTION_KEYS:
        monkeypatch.delenv(config._config_env_name(key), raising=False)


@pytest.fixture(name="provider_environment")
def configure_provider_environment(monkeypatch):
    """資格情報は架空のAPI値だけとし、開発機の設定を使わない。"""
    set_connections(monkeypatch, "subscription,credits,payg")
    monkeypatch.setenv("TEST_PROVIDER_KEY", "isolated-fake-api-key")
    monkeypatch.delenv("UNSET_PROVIDER_KEY", raising=False)


@contextlib.asynccontextmanager
async def backend_with_client(monkeypatch, client, root=None):
    """接続先のRPCと探索環境の事前実行を隔離し、実backendで接続先を選ぶ。"""
    async with codex.AppServerManager(
        root.sessions if root else {}, root._condition if root else asyncio.Condition()
    ) as backend:
        client.backend = backend

        async def ensure_client(*_args, **_kwargs):
            return client

        monkeypatch.setattr(backend, "_ensure_client", ensure_client)
        # ここでは接続先の選択だけを扱う。
        yield backend


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
    """サブスクリプションで開始済みのturnに、確定した利用上限の拒否を配送する。"""
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
        # APIキーのアカウントではopenaiをサブスクリプションとして使わず、使用順の次のAPIへ進む。
        ("openai", "apiKey", False, "paid-a"),
    ],
)
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
async def test_provider_environment_start(
    monkeypatch, tmp_path, provider_environment, primary, account, allowed, expected, launch_kind
):
    del provider_environment
    client = ProviderClient()
    client.primary, client.account_type, client.allowed = primary, account, allowed
    set_connections(monkeypatch, "payg,subscription,credits" if primary == "paid-b" else "subscription,credits,payg")
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("元の作業", str(tmp_path), None, None, launch_kind=launch_kind)
        assert session.codex_model_provider == expected
        assert client.connection == expected
        assert all(params["cwd"] == str(tmp_path) for method, params in client.requests if method == "config/read")
        if primary != "openai" or account == "apiKey":
            assert not any(method == "account/rateLimits/read" for method, _ in client.requests)
        if primary == "openai" and account == "chatgpt" and allowed is False:
            assert not any(method == "thread/resume" for method, _ in client.requests)
            assert [params["modelProvider"] for method, params in client.requests if method == "thread/start"] == ["paid-a"]


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", [True, False])
async def test_manager_turn_start_failure_finishes_without_provider_details(
    monkeypatch, tmp_path, provider_environment, subscription
):
    """RPC拒否を実監視から継続し、各API候補を一度だけ試して安全な終了を回収する。"""
    del provider_environment
    async with manager.AgentsServerManager() as root:
        client = ProviderClient()
        client.allowed = subscription
        async with backend_with_client(monkeypatch, client, root) as backend:
            install_backend(root, "codex", backend)
            original_request = client.request
            attempted_turns = []

            async def request(method, params=None, **kwargs):
                if method == "turn/start":
                    attempted_turns.append(client.connection)
                    info = (
                        "usageLimitExceeded"
                        if client.connection == "openai"
                        else {"httpConnectionFailed": {"httpStatusCode": 401}}
                    )
                    raise codex.JsonRpcResponseError(
                        method,
                        -32600,
                        f"provider {client.connection} rejected the request",
                        {
                            "codexErrorInfo": info,
                            "modelProvider": client.connection,
                            "providerDetails": {"token": "private-value"},
                        },
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
                public = json.dumps(
                    [started, result, (await root.list_sessions()), (await root.show_session(session.session_id))]
                )
                assert "modelProvider" not in public and "providerDetails" not in public
                assert "private-value" not in public and "paid-a" not in public and "paid-b" not in public
                assert all(
                    params["threadId"] == session.session_id for method, params in client.requests if method == "thread/resume"
                )
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
    async with backend_with_client(monkeypatch, client) as backend:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["rateLimitExceeded", "serverOverloaded", "unauthorized"])
async def test_subscription_non_usage_failure_does_not_switch(monkeypatch, tmp_path, provider_environment, error):
    del provider_environment
    async with backend_with_client(monkeypatch, ProviderClient()) as backend:
        session = await backend.start("元の作業", str(tmp_path), None, None)
        await complete(backend, session, {"codexErrorInfo": error})
        assert not session.codex_provider_resume_pending
        assert session.codex_model_provider == "openai"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider_mismatch", "missing_closure", "ambiguous_turn"])
async def test_resume_confirmation_mismatch_prevents_turn(monkeypatch, tmp_path, provider_environment, failure):
    del provider_environment
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
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


@pytest.mark.asyncio
async def test_interrupt_during_provider_resume_prevents_turn(monkeypatch, tmp_path, provider_environment):
    del provider_environment
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
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


@pytest.mark.parametrize("failure_source", ["notification", "request"])
@pytest.mark.asyncio
async def test_provider_failover_result_collection(monkeypatch, tmp_path, provider_environment, caplog, failure_source):
    """公開startとwaitが内部失敗を返さず、監視後の最終結果だけを回収する。"""
    del provider_environment
    async with manager.AgentsServerManager() as root:
        client = ProviderClient()
        async with backend_with_client(monkeypatch, client, root) as backend:
            install_backend(root, "codex", backend)
            pending = []
            original_request = client.request
            read_started = asyncio.Event()
            read_release = asyncio.Event()
            wait_blocked = asyncio.Event()
            public_wait_context = contextvars.ContextVar("public_wait", default=False)
            original_file_operation = codex.run_file_operation
            original_wait = root._condition.wait

            async def file_operation(function, *args: Any, **kwargs: Any):
                if function is codex.codex_usage_limit.from_thread:
                    read_started.set()
                    await read_release.wait()
                return await original_file_operation(function, *args, **kwargs)

            async def condition_wait():
                if public_wait_context.get():
                    wait_blocked.set()
                return await original_wait()

            async def public_wait():
                public_wait_context.set(True)
                return await root.wait()

            async def finish_turn():
                await asyncio.sleep(0)
                session = backend.sessions["thread-codex"]
                if client.connection == "openai":
                    error = {"codexErrorInfo": "usageLimitExceeded"}
                    if failure_source == "request":
                        await backend._mark_failed(
                            session, codex.JsonRpcResponseError("turn/start", -32600, "上限拒否", error), retryable=False
                        )
                    else:
                        await complete(backend, session, error)
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
            monkeypatch.setattr(codex, "run_file_operation", file_operation)
            monkeypatch.setattr(root._condition, "wait", condition_wait)
            monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.01)
            root.activate()
            try:
                start_task = asyncio.create_task(root.start("codex:gpt-6-sol/high", "元の作業", str(tmp_path)))
                pending.append(start_task)
                await asyncio.wait_for(read_started.wait(), 5)
                root._wait_timeouts["main"] = 3
                wait_task = asyncio.create_task(public_wait())
                pending.append(wait_task)
                blocked_task = asyncio.create_task(wait_blocked.wait())
                pending.append(blocked_task)
                await asyncio.wait_for(asyncio.wait([wait_task, blocked_task], return_when=asyncio.FIRST_COMPLETED), 5)
                returned_during_read = wait_task.done()
                read_release.set()
                started = await asyncio.wait_for(start_task, 5)
                result = await asyncio.wait_for(wait_task, 5)
                assert not returned_during_read, "利用上限の保留判定中に公開waitが復帰した"
                assert result["status"] == "completed"
                assert result["agent_message"] == "継続後の結果"
                public = json.dumps([started, result, (await root.list_sessions()), (await root.show_session("thread-codex"))])
                assert "paid-a" not in public and "codex_model_provider" not in public
                assert "isolated-fake-api-key" not in caplog.text
                assert "provider=paid-a" in caplog.text
                assert len([1 for method, _ in client.requests if method == "thread/start"]) == 1
            finally:
                read_release.set()
                for task in pending:
                    if not task.done():
                        task.cancel()
                await root.close()
                await asyncio.gather(*pending, return_exceptions=True)


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
async def test_provider_transition_preserves_pending_children(monkeypatch, tmp_path, provider_environment):
    """上限失敗が孫の保留を解除せず、移行後の次turnも追跡を維持する。"""
    del provider_environment
    async with backend_with_client(monkeypatch, ProviderClient()) as backend:
        session = await backend.start("孫の結果を待つ作業", str(tmp_path))
        session.live_child_session_ids.add("child-thread")
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        assert session.pending_result is not None and resume_waits.is_background_completion_wait(session)
        assert not session.codex_provider_resume_pending
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert response["delivery"] == "reply_started"
        assert session.live_child_session_ids == {"child-thread"}
        assert session.codex_model_provider == "paid-a"
        assert not session.codex_provider_resume_pending


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
@pytest.mark.parametrize("provider_response", [True, False])
@pytest.mark.parametrize("resume_provider_response", [True, False])
async def test_empty_provider_configuration_keeps_current_resume_settings(
    monkeypatch, tmp_path, provider_response, resume_provider_response
):
    """接続設定が無ければ応答の接続先を固定せず、再開時の通常設定を使う。"""
    clear_connections(monkeypatch)
    client = ProviderClient()
    original_request = client.request

    async def request(method, params=None, **kwargs):
        response = await original_request(method, params, **kwargs)
        if (method == "thread/start" and not provider_response) or (method == "thread/resume" and not resume_provider_response):
            response.pop("modelProvider", None)
        return response

    monkeypatch.setattr(client, "request", request)
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("通常の作業", str(tmp_path))
        assert session.codex_model_provider is None
        assert session.codex_subscription_provider is None
        await complete(backend, session)
        client.primary = "paid-b"
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started"
        assert client.connection == "paid-b"
        assert all(params["modelProvider"] == "paid-b" for method, params in client.requests if method == "thread/resume")


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", [True, False])
async def test_turn_start_failure_keeps_provider_details_private(monkeypatch, tmp_path, provider_environment, subscription):
    """構造化された失敗情報から有限回の継続を判定し、追加の接続先情報は公開結果へ入れない。"""
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
    async with backend_with_client(monkeypatch, client) as backend:
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


@pytest.mark.asyncio
async def test_provider_order_overrides_codex_default(monkeypatch, tmp_path, provider_environment):
    """Codexの通常接続がAPIでも、明示した接続先列の先頭にあるサブスクリプションで開始する。"""
    del provider_environment
    client = ProviderClient()
    client.primary = "paid-b"
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("優先順の作業", str(tmp_path))
        assert client.connection == "openai"
        assert session.codex_subscription_provider == "openai"
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started"
        assert client.connection == "paid-a"
        assert session.session_id == "thread-codex"


@pytest.mark.asyncio
@pytest.mark.parametrize(("next_order", "expected"), [("credits,subscription,payg", "openai"), ("payg,credits", "paid-b")])
async def test_api_primary_uses_ordered_api_candidates(monkeypatch, tmp_path, provider_environment, next_order, expected):
    """先頭のクレジットで失敗が確定した場合は、現在の使用順でクレジット以外の最初の候補へ同じ会話を移す。"""
    del provider_environment
    set_connections(monkeypatch, "credits,subscription,payg")
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("API優先の作業", str(tmp_path))
        assert client.connection == "paid-a" and session.codex_subscription_provider is None
        assert not any(method == "account/rateLimits/read" for method, _ in client.requests)
        set_connections(monkeypatch, next_order)
        await complete(backend, session, {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}})
        assert session.codex_provider_resume_pending
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started" and client.connection == expected
        assert [params["modelProvider"] for method, params in client.requests if method == "thread/resume"] == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["credits", "credits,payg"])
async def test_api_primary_start_rejection_is_finite(monkeypatch, tmp_path, provider_environment, order):
    """無効な候補と開始を拒否した候補があっても使用順の候補だけを試し、通常接続へ戻らない。"""
    del provider_environment
    # 未定義のproviderを持つ従量課金は候補から外れ、クレジットだけの使用順では後続が無い。
    set_connections(monkeypatch, order, payg="paid-b" if order == "credits,payg" else "missing")
    client = ProviderClient()
    client.reject.add("paid-a")
    async with backend_with_client(monkeypatch, client) as backend:
        if order == "credits":
            with pytest.raises(codex.AppServerError, match="candidates were rejected"):
                await backend.start("拒否するAPI", str(tmp_path))
        else:
            session = await backend.start("後続のAPI", str(tmp_path))
            assert session.codex_model_provider == "paid-b"
        assert [params["modelProvider"] for method, params in client.requests if method == "thread/start"] == (
            ["paid-a"] if order == "credits" else ["paid-a", "paid-b"]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stopped", "retention_expired"])
async def test_api_primary_registry_restores_after_release(monkeypatch, tmp_path, reason):
    """サブスクリプションの利用履歴が無い主APIの接続先も、解放後の公開の追送で同じ接続先を維持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        "provider-thread", terminal=True, engine="codex", cwd=str(tmp_path), codex_model_provider="paid-b", status="completed"
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


def write_resume_rollout(path: pathlib.Path, identifier: str, provider: str | None, item: dict[str, Any] | None) -> bytes:
    """CLI 0.164.0のsession_metaとresponse_itemを、暗号化内容を模した値で保存する。"""
    records = [{"type": "session_meta", "payload": {"id": identifier, "model_provider": provider}}]
    if item is not None:
        records.append(item if item.get("type") == "compacted" else {"type": "response_item", "payload": item})
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path.read_bytes()


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
        terminal=True,
        engine="codex",
        cwd=str(tmp_path),
        model="gpt-6-sol",
        model_type="codex:gpt-6-sol/high",
        effort="high",
        turn_seq=1,
        codex_model_provider=None if lifecycle == "settings" else "paid-b",
        codex_state_provider=None if lifecycle == "legacy_migration" else "openai",
        status="completed",
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
        terminal=True,
        engine="codex",
        cwd=str(tmp_path),
        codex_state_provider="paid-b",
        status="completed",
    )
    session_registry.release("verified-thread", reason="stopped")
    async with manager.AgentsServerManager() as root:
        restored = await root._restore_registry_session("verified-thread")
        assert restored is not None and restored.codex_state_provider == "paid-b"
        assert restored.codex_model_provider is None
        assert "codex_state_provider" not in json.dumps(await root.show_session("verified-thread"))


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
@pytest.mark.parametrize(
    ("credits_provider", "payg", "account", "expected"),
    [
        # 使える種別が1つ・2つ・3つの環境で、同じ使用順のまま未設定の種別だけを除く。
        ("", "", "chatgpt", ["openai"]),
        ("paid-a", "", "apiKey", ["paid-a"]),
        ("", "paid-b", "apiKey", ["paid-b"]),
        ("paid-a", "", "chatgpt", ["paid-a", "openai"]),
        ("paid-a", "paid-b", "apiKey", ["paid-a", "paid-b"]),
        ("paid-a", "paid-b", "chatgpt", ["paid-a", "openai", "paid-b"]),
    ],
)
async def test_select_follows_order_and_skips_unconfigured_kinds(
    monkeypatch, tmp_path, provider_environment, credits_provider, payg, account, expected
):
    del provider_environment
    set_connections(monkeypatch, "credits,subscription,payg", credits_provider=credits_provider, payg=payg)
    client = ProviderClient()
    client.account_type = account
    selection = await codex_providers.select(client.request, str(tmp_path))
    assert [selection.primary, *selection.candidates] == expected


@pytest.mark.asyncio
async def test_payg_is_selected_only_when_listed(monkeypatch, tmp_path, provider_environment):
    """従量課金のproviderを設定しても、初期値の使用順（paygを含まない）では候補にしない。"""
    del provider_environment
    clear_connections(monkeypatch)
    monkeypatch.setenv(config._config_env_name("codex_payg_provider"), "paid-b")
    client = ProviderClient()
    selection = await codex_providers.select(client.request, str(tmp_path))
    assert (selection.primary, selection.candidates) == ("openai", ())
    client.account_type = "apiKey"
    with pytest.raises(ValueError, match="codex_worker_connections"):
        await codex_providers.select(client.request, str(tmp_path))


@pytest.mark.asyncio
async def test_api_key_account_excludes_subscription_with_diagnostic(monkeypatch, tmp_path, provider_environment, caplog):
    """APIキーのアカウントではsubscriptionを除き、openaiをAPI種別へ設定する方法を診断へ出す。"""
    del provider_environment
    set_connections(monkeypatch, "subscription,payg")
    client = ProviderClient()
    client.account_type = "apiKey"
    selection = await codex_providers.select(client.request, str(tmp_path))
    assert selection.primary == "paid-b" and not selection.subscription
    assert selection.diagnostics == (codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC,)
    assert "codex_payg_provider openai" in caplog.text


@pytest.mark.asyncio
async def test_credit_rejection_moves_session_and_reorders_new_sessions(monkeypatch, tmp_path, provider_environment):
    """クレジットの不受理で同じ会話を使用順の次へ移し、1時間は新しい会話でクレジットを後ろへ回す。"""
    del provider_environment
    set_connections(monkeypatch, "credits,subscription")
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("クレジットの作業", str(tmp_path))
        assert session.codex_model_provider == "paid-a"
        await complete(backend, session, {"codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": 429}}})
        assert session.codex_provider_resume_pending
        assert api_credits.shortage_active("codex")
        assert not api_credits.shortage_active("claude")
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started" and session.codex_model_provider == "openai"
    fresh = await codex_providers.select(ProviderClient().request, str(tmp_path))
    assert (fresh.primary, fresh.candidates) == ("openai", ("paid-a",))
    continuing = await codex_providers.select(ProviderClient().request, str(tmp_path), primary="paid-a")
    assert continuing.primary == "paid-a"
    api_credits.clear()
    restored = await codex_providers.select(ProviderClient().request, str(tmp_path))
    assert restored.primary == "paid-a"


@pytest.mark.asyncio
async def test_subscription_limit_after_credit_shortage_follows_effective_order(monkeypatch, tmp_path, provider_environment):
    """残高不足の観測中に3種別で始めた会話は、サブスクの利用上限で後ろへ回したクレジットより先に従量課金へ移る。"""
    del provider_environment
    set_connections(monkeypatch, "credits,subscription,payg")
    api_credits.record_shortage("codex")
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("元の作業", str(tmp_path))
        assert session.codex_model_provider == "openai"
        await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert response["delivery"] == "reply_started"
        assert session.codex_model_provider == "paid-b"
    continuing = await codex_providers.select(ProviderClient().request, str(tmp_path), primary="openai")
    assert continuing.candidates == ("paid-b", "paid-a")


@pytest.mark.asyncio
async def test_session_moves_when_its_kind_leaves_the_order_and_does_not_return(monkeypatch, tmp_path, provider_environment):
    """保持した種別を使用順から外した会話は次の継続で先頭の候補へ移り、使用順を戻しても元の種別へ戻らない。"""
    del provider_environment
    set_connections(monkeypatch, "credits,subscription")
    client = ProviderClient()
    async with backend_with_client(monkeypatch, client) as backend:
        session = await backend.start("クレジットの作業", str(tmp_path))
        assert session.codex_model_provider == "paid-a"
        await complete(backend, session)
        set_connections(monkeypatch, "subscription")
        response = await backend.send_message(session, "続き")
        assert response["delivery"] == "reply_started"
        assert session.codex_model_provider == "openai" and session.codex_subscription_provider == "openai"
        await complete(backend, session)
        set_connections(monkeypatch, "credits,subscription")
        response = await backend.send_message(session, "さらに続き")
        assert response["delivery"] == "reply_started" and session.codex_model_provider == "openai"
    assert [params["modelProvider"] for method, params in client.requests if method == "thread/resume"] == [
        "openai",
        "openai",
    ]


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
