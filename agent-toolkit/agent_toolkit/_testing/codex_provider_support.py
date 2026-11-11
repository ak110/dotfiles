"""Codex接続先の試験でRPCと保存会話を準備する。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
from typing import Any

from agent_toolkit._agents_server import codex
from agent_toolkit._atk import config
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_support import FakeCodexClient

# 公開操作へ渡す状態とRPC応答を準備する。
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
        self.start_mismatch = False
        self.reject_data: Any = None
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
                raise codex.JsonRpcResponseError(method, -32600, "model is not accepted", self.reject_data)
            result["modelProvider"] = "unrequested" if self.start_mismatch else self.connection
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


def set_connections(monkeypatch, order: str, *, credits_provider: str = "paid-a", payg: str = "paid-b") -> None:
    """Codexの接続設定を、クレジット・従量課金のproviderと委譲先の使用順で与える。"""
    monkeypatch.setenv(config._config_env_name("codex_credits_provider"), credits_provider)
    monkeypatch.setenv(config._config_env_name("codex_payg_provider"), payg)
    monkeypatch.setenv(config._config_env_name("codex_worker_connections"), order)


def clear_connections(monkeypatch) -> None:
    """Codexの接続設定を全て外し、config.tomlの通常設定へ戻す。"""
    for key in _CODEX_CONNECTION_KEYS:
        monkeypatch.delenv(config._config_env_name(key), raising=False)


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
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="codex",
        cwd=str(tmp_path),
        codex_model_provider="paid-b",
        codex_subscription_provider="openai",
    )


async def fail_subscription(backend, tmp_path):
    """サブスクリプションで開始済みのturnに、確定した利用上限の拒否を配送する。"""
    session = await backend.start("元の作業", str(tmp_path), None, None)
    await complete(backend, session, {"codexErrorInfo": "usageLimitExceeded"})
    return session


def write_resume_rollout(path: pathlib.Path, identifier: str, provider: str | None, item: dict[str, Any] | None) -> bytes:
    """CLI 0.164.0のsession_metaとresponse_itemを、暗号化内容を模した値で保存する。"""
    records = [{"type": "session_meta", "payload": {"id": identifier, "model_provider": provider}}]
    if item is not None:
        records.append(item if item.get("type") == "compacted" else {"type": "response_item", "payload": item})
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path.read_bytes()


_CODEX_CONNECTION_KEYS = (
    "codex_credits_provider",
    "codex_payg_provider",
    "codex_orchestrator_connections",
    "codex_worker_connections",
)
