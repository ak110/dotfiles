"""Codex接続先の選定と起動を検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._agents_server import api_credits, codex, codex_providers
from agent_toolkit._atk import codex_connection, config
from agent_toolkit._testing.codex_provider_support import (
    ProviderClient,
    backend_with_client,
    clear_connections,
    complete,
    set_connections,
)

# 公開結果とRPCの内部状態を比較する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


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
            with pytest.raises(codex.AppServerError, match="candidates were rejected") as raised:
                await backend.start("拒否するAPI", str(tmp_path))
            # APIの拒否は利用上限による候補の不成立と区別し、次のengineへ進める型にしない。
            assert raised.type is codex.AppServerError
            assert "paid-a: code=-32600: thread/start: model is not accepted" in str(raised.value)
        else:
            session = await backend.start("後続のAPI", str(tmp_path))
            assert session.codex_model_provider == "paid-b"
        assert [params["modelProvider"] for method, params in client.requests if method == "thread/start"] == (
            ["paid-a"] if order == "credits" else ["paid-a", "paid-b"]
        )


@pytest.mark.asyncio
async def test_usage_limit_without_other_connection_is_candidate_unavailable(monkeypatch, tmp_path, provider_environment):
    """利用上限で主候補を省き試す接続先が残らない起動は、理由と解除予定を持つ候補の不成立として返す。"""
    del provider_environment
    set_connections(monkeypatch, "subscription")
    monkeypatch.setenv(config._config_env_name("codex_usage_limit_wait"), "five-hour")
    client = ProviderClient()
    client.allowed = False
    client.window_minutes = 10080
    async with backend_with_client(monkeypatch, client) as backend:
        with pytest.raises(codex.StartCandidateUnavailable) as raised:
            await backend.start("上限時の起動", str(tmp_path))
    error = raised.value
    assert error.scope == "candidate"
    for expected in (
        "provider=openai",
        "ordinaryUsageAllowed=false",
        "上限種別=seven_day",
        "解除予定=1970-01-01T00:00:01+00:00",
        "`codex_usage_limit_wait`=five-hour",
        "`codex_worker_connections`に他の利用可能な接続先が無い",
    ):
        assert expected in error.reason
    assert "rejected" not in error.reason
    assert error.next_action is not None
    assert "解除予定" in error.next_action and "別のengine" in error.next_action and "認証" not in error.next_action
    assert not any(method == "thread/start" for method, _ in client.requests)
    # 記録済みの理由は保持期間の間、Codexの確認を省いて次の候補から始める根拠になる。
    assert codex.AppServerManager.excludes_with_recorded_reason(error.reason)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["all_rejected", "primary_rejected", "connection_mismatch"])
async def test_start_abandonment_other_than_usage_limit_stays_error(
    monkeypatch, tmp_path, provider_environment, caplog, branch
):
    """接続先の拒否と応答の契約違反は、次のengineへ進める型にせず原因を保ったエラーで返す。"""
    del provider_environment
    monkeypatch.setenv(config._config_env_name("codex_usage_limit_wait"), "five-hour")
    client = ProviderClient()
    if branch == "all_rejected":
        client.allowed = False
        client.window_minutes = 10080
        client.reject.update({"paid-a", "paid-b"})
        client.reject_data = {"codexErrorInfo": "unauthorized", "providerDetails": {"token": "private-value"}}
    elif branch == "primary_rejected":
        client.reject.add("openai")
    else:
        client.start_mismatch = True
    caplog.set_level("INFO", logger="agent-toolkit.agents-server.codex")
    async with backend_with_client(monkeypatch, client) as backend:
        with pytest.raises(codex.AppServerError) as raised:
            await backend.start("拒否される起動", str(tmp_path))
    error = raised.value
    assert not isinstance(error, codex.StartCandidateUnavailable)
    message = str(error)
    started = [params["modelProvider"] for method, params in client.requests if method == "thread/start"]
    if branch == "all_rejected":
        assert raised.type is codex.AppServerError
        assert started == ["paid-a", "paid-b"]
        for provider in started:
            assert f'{provider}: code=-32600: thread/start: model is not accepted (codexErrorInfo="unauthorized")' in message
        assert "openai: 試していない" in message and "上限種別=seven_day" in message
        assert "private-value" not in message and "providerDetails" not in message
        assert (
            "Codex接続先の開始が拒否されました: provider=paid-a code=-32600 reason=thread/start: model is not accepted"
            ' codexErrorInfo="unauthorized"'
        ) in caplog.text
        assert "private-value" not in caplog.text
    elif branch == "primary_rejected":
        assert isinstance(error, codex.JsonRpcResponseError)
        assert started == ["openai"] and message == "thread/start: model is not accepted"
        assert "provider=openai code=-32600 reason=thread/start: model is not accepted" in caplog.text
    else:
        assert raised.type is codex.AppServerError
        assert message == "thread/start did not confirm the requested connection"


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
