"""Codex接続先の実効設定・認証境界と有限な代替候補を解釈する。

Codex CLI 0.162.0のconfig/read・account/read・account/rateLimits/readの
公開schemaを入力契約とする。資格情報を複製せず、Codexが持つprovider定義を使う。
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._agents_server import engine_availability
from agent_toolkit._atk import config as atk_config

CONTINUE_PROMPT = "以前の会話と実行結果を確認し、完了済みの操作を繰り返さず、未完了の作業を続けて所定の返却形式を返せ。"
_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


@dataclasses.dataclass(frozen=True)
class ProviderSelection:
    """実効接続先と、通常接続がChatGPT認証を使うかの判定。"""

    primary: str
    subscription: bool
    candidates: tuple[str, ...]
    ordinary_usage_allowed: bool | None = None


def configured_candidates() -> tuple[str, ...]:
    """atkの変更可能設定を接続先列として解決する。"""
    legacy = atk_config.legacy_codex_provider_setting()
    if legacy is not None:
        return legacy[0]
    return atk_config.parse_codex_provider_candidates(atk_config.resolve_mutable_setting("codex_model_providers"))


def has_provider_configuration() -> bool:
    """明示列か旧形式の移行入力がある場合だけCodex設定を評価する。"""
    return bool(configured_candidates()) or atk_config.legacy_codex_provider_setting() is not None


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


async def select(
    request: Callable[..., Awaitable[dict[str, Any]]], cwd: str, *, primary: str | None = None
) -> ProviderSelection:
    """cwdを含むCodex設定と現在の認証種別から接続先を選ぶ。"""
    response = await request("config/read", {"cwd": cwd, "includeLayers": False})
    effective = response.get("config")
    if not isinstance(effective, dict):
        raise ValueError("Codexの実効接続先設定を取得できません")
    default = effective.get("model_provider") or "openai"
    legacy = atk_config.legacy_codex_provider_setting()
    configured = atk_config.migrate_codex_provider_setting(default)
    if legacy is not None:
        _LOG.info("Codex接続先設定を新しい優先順へ移行しました: source=%s", "saved" if legacy[1] else "environment")
    selected = primary or (configured[0] if configured else default)
    providers = effective.get("model_providers", {})
    providers = providers if isinstance(providers, dict) else {}
    auth = await request("account/read", {"refreshToken": False})
    account = auth.get("account")
    definition = providers.get(selected, {})
    requires_auth = definition.get("requires_openai_auth", selected == "openai") if isinstance(definition, dict) else False
    subscription = bool(requires_auth and isinstance(account, dict) and account.get("type") == "chatgpt")

    def usable_api(candidate: str) -> bool:
        definition = providers.get(candidate)
        if candidate == "openai" and isinstance(account, dict) and account.get("type") == "apiKey":
            return True
        return isinstance(definition, dict) and independent_api_auth(definition)

    if primary is None and configured and not subscription and not usable_api(selected):
        _LOG.info("Codex接続先を除外しました: provider=%s reason=undefined_or_no_independent_api_auth", selected)
        selected = next((candidate for candidate in configured[1:] if usable_api(candidate)), "")
        if not selected:
            raise ValueError("Codexの指定した接続先に利用可能なAPI認証がありません")
    remainder = configured[configured.index(selected) + 1 :] if primary is None and selected in configured else configured
    candidates = []
    for candidate in remainder:
        if candidate == selected:
            continue
        if not usable_api(candidate):
            _LOG.info("Codex接続先を除外しました: provider=%s reason=undefined_or_no_independent_api_auth", candidate)
            continue
        candidates.append(candidate)
    allowed = None
    if subscription:
        try:
            usage = await request("account/rateLimits/read", {})
        except (RuntimeError, OSError, TimeoutError):
            usage = {}
        # ordinaryUsageAllowedはactive accountへの検証済み欄。accountIdが明示される場合は
        # account/readのworkspaceRoutingと同じIDの結果だけを使い、疎通知を補完に使わない。
        routing = auth.get("workspaceRouting")
        active_id = routing.get("chatgptAccountId") if isinstance(routing, dict) else None
        snapshot_id = usage.get("accountId")
        if snapshot_id is None or active_id is not None and snapshot_id == active_id:
            value = usage.get("ordinaryUsageAllowed")
            allowed = value if isinstance(value, bool) else None
    return ProviderSelection(selected, subscription, tuple(candidates), allowed)


def fallback_failure(error: Any, *, subscription: bool) -> bool:
    """通常サブスクでは利用上限だけ、移行済みAPIでは候補の不受理も次候補へ接続する。"""
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
