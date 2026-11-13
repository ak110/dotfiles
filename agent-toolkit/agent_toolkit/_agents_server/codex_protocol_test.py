"""Codexの拒否診断が公開結果とログへ資格情報を残さないことを検証する。"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from agent_toolkit._agents_server import codex, manager
from agent_toolkit._testing.agents_server_support import install_backend
from agent_toolkit._testing.codex_provider_support import ProviderClient, backend_with_client

# 公開start/waitを実行するため、実backendをテスト用RPCへ接続する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    "info",
    [{"apiKey": "short synthetic value"}, 'token="short synthetic value"', {"Authorization": "Bearer short-synthetic-value"}],
)
def test_jsonrpc_diagnostic_redacts_encoded_credentials(info):
    """構造化dataとその文字列内の認証値を、code・拒否理由を保持して伏せる。"""
    error = codex.JsonRpcResponseError("turn/start", -32600, "authentication denied\n401", {"codexErrorInfo": info})
    for diagnostic in error.diagnostic():
        assert "short synthetic value" not in diagnostic and "short-synthetic-value" not in diagnostic
        assert "code=-32600" in diagnostic and "authentication denied" in diagnostic and "401" in diagnostic
        assert "\n" not in diagnostic


@pytest.mark.parametrize(
    ("stage", "rpc"),
    [("thread/start", True), ("thread/resume", True), ("turn/start", True), ("turn/start", False)],
)
@pytest.mark.asyncio
async def test_rejection_diagnostics_redact_credentials(monkeypatch, tmp_path, provider_environment, caplog, stage, rpc):
    """合成した拒否のcode・原因を保ち、全記録経路から認証値を取り除く。"""
    del provider_environment
    # 実際の資格情報は使わず、短い値・引用符内の空白・無名の長い鍵状文字列も含める。
    secrets = [
        "synthetic-bearer-value",
        "synthetic-basic-value",
        "synthetic-header-value",
        "synthetic api value",
        "synthetic-access-value",
        "synthetic-secret-value",
        "synthetic password value",
        "AbCd0123456789" * 4,
        "synthetic-nested-value",
    ]
    reason = (
        "authentication denied (401); "
        f"Authorization: Bearer {secrets[0]}; Proxy-Authorization: Basic {secrets[1]}; "
        f"X-API-Key={secrets[2]}; "
        f'{{"api_key": "{secrets[3]}", "access_token": "{secrets[4]}"}}; '
        f"client_secret={secrets[5]}; password='{secrets[6]}'; opaque={secrets[7]}"
    )
    caplog.set_level(logging.INFO, logger="agent-toolkit.agents-server.codex")
    async with manager.AgentsServerManager() as root:
        client = ProviderClient()
        client.allowed = False
        async with backend_with_client(monkeypatch, client, root) as backend:
            install_backend(root, "codex", backend)
            original_request = client.request

            async def request(method, params=None, **kwargs):
                if method == stage:
                    if not rpc:
                        # 接続喪失が確定した一般例外は、受理状態が曖昧なturnとして保留しない。
                        client.reader_failure = OSError("connection closed")
                        raise OSError(reason)
                    raise codex.JsonRpcResponseError(
                        method,
                        -32600,
                        reason,
                        {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401, "token": secrets[8]}}},
                    )
                if stage == "thread/resume" and method == "turn/start":
                    # 初回のAPI拒否から接続切替を開始し、再開の拒否も公開waitまで通す。
                    raise codex.JsonRpcResponseError(
                        method, -32600, "initial refusal", {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}
                    )
                return await original_request(method, params, **kwargs)

            monkeypatch.setattr(client, "request", request)
            monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.01)
            root.activate()
            if stage == "thread/start":
                with pytest.raises(codex.AppServerError) as raised:
                    await asyncio.wait_for(root.start("codex:gpt-6-sol/high", "作業", str(tmp_path)), 5)
                public = str(raised.value)
            else:
                started = await asyncio.wait_for(root.start("codex:gpt-6-sol/high", "作業", str(tmp_path)), 5)
                root._wait_timeouts["main"] = 3
                result = await asyncio.wait_for(root.wait(), 5)
                assert result["status"] == "failed"
                public = json.dumps([started, result, await root.list_sessions(), await root.show_session("thread-codex")])
                assert "authentication denied" not in public
                assert "paid-a" not in public and "paid-b" not in public
            logged = [record.getMessage() for record in caplog.records if "authentication denied" in record.getMessage()]
            assert logged
            assert all("401" in line for line in logged)
            assert all(("code=-32600" if rpc else "OSError") in line for line in logged)
            for value in secrets:
                assert value not in public
                assert value not in caplog.text
                assert all(value not in str(record.args) for record in caplog.records)
