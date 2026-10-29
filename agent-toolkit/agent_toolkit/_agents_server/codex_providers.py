"""Codex接続先の実効設定・認証境界と有限な代替候補を解釈する。

Codex CLI 0.162.0のconfig/read・account/read・account/rateLimits/readの
公開schemaを入力契約とする。資格情報を複製せず、Codexが持つprovider定義を使う。
種別とprovider IDの対応は`agent_toolkit._atk.codex_connection`が設定から解決する。
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._agents_server import api_credits, engine_availability
from agent_toolkit._atk import codex_connection
from agent_toolkit._atk import config as atk_config
from agent_toolkit._common import codex_usage_limit

CONTINUE_PROMPT = "以前の会話と実行結果を確認し、完了済みの操作を繰り返さず、未完了の作業を続けて所定の返却形式を返せ。"
_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


@dataclasses.dataclass(frozen=True)
class ProviderSelection:
    """実効接続先と、通常接続がChatGPT認証を使うかの判定。

    `candidates`は使用順で`primary`より後ろにある利用可能なprovider ID、`diagnostics`は候補から除いた理由である。
    """

    primary: str
    subscription: bool
    candidates: tuple[str, ...]
    ordinary_usage_allowed: bool | None = None
    usage_limit_type: str | None = None
    diagnostics: tuple[str, ...] = ()


def has_provider_configuration() -> bool:
    """接続設定を明示した場合だけ、使用順と種別からproviderを選ぶ。"""
    return codex_connection.managed()


def independent_api_auth(provider: dict[str, Any]) -> bool:
    """ChatGPT認証を要求せず、接続先自身のAPI認証が与えられているかを返す。

    認証の内容は呼び出し元へ返さず、環境変数をCodexの子プロセスが解決する既存方式を保つ。
    """
    if provider.get("requires_openai_auth", False):
        return False
    env_key = provider.get("env_key")
    if isinstance(env_key, str) and os.environ.get(env_key):
        return True
    if provider.get("experimental_bearer_token"):
        return True
    headers = provider.get("http_headers")
    if isinstance(headers, dict) and any(key.lower() == "authorization" and value for key, value in headers.items()):
        return True
    env_headers = provider.get("env_http_headers")
    return isinstance(env_headers, dict) and any(
        key.lower() == "authorization" and isinstance(value, str) and os.environ.get(value)
        for key, value in env_headers.items()
    )


def _entry_kind(entry: tuple[str, str]) -> str:
    """残高不足の並べ替えへ渡す候補の種別を返す。"""
    return entry[0]


async def select(
    request: Callable[..., Awaitable[dict[str, Any]]],
    cwd: str,
    *,
    primary: str | None = None,
    role: str = "delegate",
) -> ProviderSelection:
    """cwdを含むCodex設定と現在の認証種別から、役割の使用順に沿って接続先を選ぶ。

    `primary`を渡す継続の会話はそのproviderを使い続け、後続候補だけを使用順から求める。
    委譲先では、APIクレジットの残高不足を観測してから1時間は`credits`を使用順の後ろへ回す。
    新規の会話の開始順と、失敗した会話が次に試す後続候補の順を同じ実効順序にそろえ、
    並べ替えで後ろへ回した`credits`を後続候補で先に再試行しないようにする。
    """
    response = await request("config/read", {"cwd": cwd, "includeLayers": False})
    effective = response.get("config")
    if not isinstance(effective, dict):
        raise ValueError("Codexの実効接続先設定を取得できません")
    providers = effective.get("model_providers", {})
    providers = providers if isinstance(providers, dict) else {}
    auth = await request("account/read", {"refreshToken": False})
    account = auth.get("account")
    account_type = account.get("type") if isinstance(account, dict) else None
    entries = codex_connection.provider_entries(role)
    if role == "delegate":
        entries = api_credits.order(entries, _entry_kind, engine="codex")
    diagnostics: list[str] = []

    def usable(kind: str, candidate: str) -> bool:
        if kind == "subscription":
            if account_type != "chatgpt":
                if account_type is not None and codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC not in diagnostics:
                    diagnostics.append(codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC)
                    _LOG.warning(codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC)
                return False
            return True
        definition = providers.get(candidate)
        if candidate == "openai" and account_type == "apiKey":
            return True
        if isinstance(definition, dict) and independent_api_auth(definition):
            return True
        _LOG.info("Codex接続先を除外しました: provider=%s reason=undefined_or_no_independent_api_auth", candidate)
        return False

    available = [candidate for kind, candidate in entries if usable(kind, candidate)]
    if primary is None:
        if not available:
            key = atk_config.connection_order_key("codex", role)
            raise ValueError(
                "Codexの使用順に利用可能な接続がありません。"
                + "".join(f"{message}。" for message in diagnostics)
                + f"`atk config set {key} <種別列>`と各種別のprovider設定・認証を確認する"
            )
        selected = available[0]
        remainder = available[1:]
    else:
        selected = primary
        remainder = [candidate for candidate in available if candidate != primary]
    definition = providers.get(selected, {})
    requires_auth = definition.get("requires_openai_auth", selected == "openai") if isinstance(definition, dict) else False
    subscription = bool(requires_auth and account_type == "chatgpt")
    allowed = None
    usage_limit_type = None
    if subscription:
        try:
            usage = await request("account/rateLimits/read", {})
        except RuntimeError, OSError, TimeoutError:
            usage = {}
        # ordinaryUsageAllowedはactive accountについて検証済みの欄である。accountIdが明示される場合は
        # account/readのworkspaceRoutingと同じIDの結果だけを使い、IDが一致しない結果で補完しない。
        routing = auth.get("workspaceRouting")
        active_id = routing.get("chatgptAccountId") if isinstance(routing, dict) else None
        snapshot_id = usage.get("accountId")
        if snapshot_id is None or active_id is not None and snapshot_id == active_id:
            value = usage.get("ordinaryUsageAllowed")
            allowed = value if isinstance(value, bool) else None
            usage_limit_type = codex_usage_limit.from_rate_limits(usage.get("rateLimits")).limit_type
    return ProviderSelection(selected, subscription, tuple(remainder), allowed, usage_limit_type, tuple(diagnostics))


def fallback_failure(error: Any, *, subscription: bool) -> bool:
    """サブスクリプションの接続先では利用上限の失敗だけを、移行済みAPIの接続先では候補が受け付けなかった失敗も、次の候補へ進む失敗と判定する。"""
    if not isinstance(error, dict):
        return False
    if subscription:
        return error.get("codexErrorInfo") == "usageLimitExceeded"
    info = error.get("codexErrorInfo")
    if isinstance(info, dict):
        return any(
            isinstance(detail, dict) and detail.get("httpStatusCode") in engine_availability.ENGINE_UNAVAILABLE_API_ERROR_STATUS
            for detail in info.values()
        )
    return (
        engine_availability.error_info_reason(error) is not None
        or engine_availability.rejected_parameter(error) == "model"
        or engine_availability.unavailable_api_status(error) is not None
        or error.get("codexErrorInfo") == "unauthorized"
    )
