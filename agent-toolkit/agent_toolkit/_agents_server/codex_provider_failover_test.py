"""Codex接続先のfailoverを検証する。"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from typing import Any

import pytest

from agent_toolkit._agents_server import codex, codex_connections, codex_providers, codex_reset, manager, resume_waits
from agent_toolkit._testing.agents_server_support import install_backend
from agent_toolkit._testing.codex_provider_support import ProviderClient, backend_with_client, complete, fail_subscription

# 公開結果とRPCの内部状態を比較する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_turn_start_rejection_keeps_diagnostic_log_for_generic_error(monkeypatch, tmp_path, provider_environment, caplog):
    """上限中のサブスクリプションを除いた従量課金の拒否は、公開errorへ接続先を出さず診断ログへ残す。"""
    del provider_environment
    caplog.set_level(logging.INFO, logger="agent-toolkit.agents-server.codex")
    async with manager.AgentsServerManager() as root:
        client = ProviderClient()
        client.allowed = False
        async with backend_with_client(monkeypatch, client, root) as backend:
            install_backend(root, "codex", backend)
            original_request = client.request

            async def request(method, params=None, **kwargs):
                if method == "turn/start":
                    raise codex.JsonRpcResponseError(
                        method,
                        -32600,
                        "payg provider refused the turn",
                        {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}},
                    )
                return await original_request(method, params, **kwargs)

            monkeypatch.setattr(client, "request", request)
            monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.01)
            root.activate()
            try:
                started = await asyncio.wait_for(root.start("codex:gpt-6-sol/high", "元の作業", str(tmp_path)), 5)
                root._wait_timeouts["main"] = 3
                result = await asyncio.wait_for(root.wait(), 5)
                session = root.sessions["thread-codex"]
                message = result["error"]["message"]
                assert message.startswith("Codex connection rejected the request")
                assert f"session_id={session.session_id}" in message
                assert "paid-a" not in message and "payg provider refused" not in message
                assert "paid-a" not in json.dumps(started)
                logged = [
                    r.getMessage()
                    for r in caplog.records
                    if session.session_id in r.getMessage() and "汎用文言" in r.getMessage()
                ]
                # 従量課金の候補ごとの拒否が1行ずつ残る。
                assert len(logged) == 2
                assert "provider=paid-a" in logged[0] and "provider=paid-b" in logged[1]
                assert all("payg provider refused the turn" in line and "code=-32600" in line for line in logged)
            finally:
                await root.close()


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
async def test_provider_failover_sequence(monkeypatch, tmp_path, provider_environment, caplog):
    del provider_environment
    client = ProviderClient()
    client.reject.add("paid-a")
    caplog.set_level("INFO", logger="agent-toolkit.agents-server.codex")
    async with backend_with_client(monkeypatch, client) as backend:
        session = await fail_subscription(backend, tmp_path)
        assert session.status == "running" and session.pending_result is not None
        response = await backend.send_message(session, codex_providers.CONTINUE_PROMPT)
        assert response["delivery"] == "reply_started"
        resumes = [params["modelProvider"] for method, params in client.requests if method == "thread/resume"]
        assert resumes == ["paid-a", "paid-b"]
        assert (
            f"Codex接続先の再開が拒否されました: session_id={session.session_id} provider=paid-a"
            " code=-32600 reason=thread/resume: model is not accepted"
        ) in caplog.text
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
        monkeypatch.setattr(codex_connections, "DEFAULT_WAIT_TIMEOUT", 0.01)
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
            original_file_operation = codex_reset.run_file_operation
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
                        await backend._mark_failed(session, codex.JsonRpcResponseError("turn/start", -32600, "上限拒否", error))
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
            monkeypatch.setattr(codex_reset, "run_file_operation", file_operation)
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
