"""Codex接続先の実効設定・認証境界と有限な代替候補を解釈する。

Codex CLI 0.162.0のconfig/read・account/read・account/rateLimits/readの
公開schemaを入力契約とする。資格情報を複製せず、Codexが持つprovider定義を使う。
種別とprovider IDの対応は`agent_toolkit._atk.codex_connection`が設定から解決する。
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._agents_server import api_credits, codex_auto_reset, engine_availability
from agent_toolkit._agents_server.codex_protocol import AppServerError
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._atk import codex_connection, usage_refresh
from agent_toolkit._atk import config as atk_config
from agent_toolkit._common import codex_fast_mode, codex_usage_limit, runtime_environment, state_paths

CONTINUE_PROMPT = "以前の会話と実行結果を確認し、完了済みの操作を繰り返さず、未完了の作業を続けて所定の返却形式を返せ。"
_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


@dataclasses.dataclass(frozen=True)
class ProviderSelection:
    """実効接続先と、通常接続がChatGPT認証を使うかの判定。

    `candidates`は使用順で`primary`より後ろにある利用可能なprovider ID、`diagnostics`は候補から除いた理由である。
    `usage_limit_resets_at`は枯渇した利用枠の解除予定（UNIX秒）で、取得できない場合は`None`である。
    """

    primary: str
    subscription: bool
    candidates: tuple[str, ...]
    ordinary_usage_allowed: bool | None = None
    usage_limit_type: str | None = None
    diagnostics: tuple[str, ...] = ()
    usage_limit_resets_at: int | None = None
    auto_reset_permitted: bool = False
    auth: dict[str, Any] | None = dataclasses.field(default=None, repr=False, compare=False)
    quota_workspace: str | None = dataclasses.field(default=None, repr=False, compare=False)


def has_provider_configuration() -> bool:
    return codex_connection.managed()


def independent_api_auth(provider: dict[str, Any]) -> bool:
    """ChatGPT認証を要求せず、接続先自身のAPI認証が与えられているかを返す。

    認証の内容は呼び出し元へ返さず、環境変数をCodexの子プロセスが解決する既存方式を保つ。
    """
    if provider.get("requires_openai_auth", False):
        return False
    env_key = provider.get("env_key")
    if isinstance(env_key, str) and runtime_environment.environment().get(env_key):
        return True
    if provider.get("experimental_bearer_token"):
        return True
    headers = provider.get("http_headers")
    if isinstance(headers, dict) and any(key.lower() == "authorization" and value for key, value in headers.items()):
        return True
    env_headers = provider.get("env_http_headers")
    return isinstance(env_headers, dict) and any(
        key.lower() == "authorization" and isinstance(value, str) and runtime_environment.environment().get(value)
        for key, value in env_headers.items()
    )


def _entry_kind(entry: tuple[str, str]) -> str:
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
    response = await codex_fast_mode.read_with_retry(request, "config/read", {"cwd": cwd, "includeLayers": False})
    effective = response.get("config")
    if not isinstance(effective, dict):
        raise ValueError("Codexの実効接続先設定を取得できません")
    providers = effective.get("model_providers", {})
    providers = providers if isinstance(providers, dict) else {}
    quota_workspace = None
    usage = None
    auth_error = None

    async def read_account(method: str, params: dict[str, Any]) -> dict[str, Any]:
        result = await request(method, params)
        value = result.get("account")
        if "account" not in result or (value is not None and not isinstance(value, dict)):
            raise ValueError("Codexの認証応答にアカウントがありません")
        return result

    try:
        auth = await codex_fast_mode.read_with_retry(read_account, "account/read", {"refreshToken": False})
    except (OSError, RuntimeError, ValueError) as error:
        usage = await codex_fast_mode.read_workspace_quota(request)
        quota_workspace = codex_fast_mode.quota_workspace_id(usage)
        auth_error = error
        auth = {}
    account = auth.get("account")
    account_type = "chatgpt" if quota_workspace is not None else account.get("type") if isinstance(account, dict) else None
    entries = codex_connection.provider_entries(role)
    if role == "delegate":
        entries = api_credits.order(entries, _entry_kind, engine="codex")
    diagnostics: list[str] = []

    def requires_subscription(candidate: str) -> bool:
        definition = providers.get(candidate, {})
        return isinstance(definition, dict) and bool(definition.get("requires_openai_auth", candidate == "openai"))

    if auth_error is None:
        await codex_fast_mode.observe_selection_auth(
            tuple(candidate for kind, candidate in entries if kind == "subscription" and requires_subscription(candidate)),
            auth,
        )

    def usable(kind: str, candidate: str) -> bool | None:
        if kind == "subscription":
            if not requires_subscription(candidate):
                return False
            if auth_error is not None:
                return True if quota_workspace is not None else None
            if account_type != "chatgpt":
                if account_type is not None and codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC not in diagnostics:
                    diagnostics.append(codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC)
                    _LOG.warning(codex_connection.API_KEY_SUBSCRIPTION_DIAGNOSTIC)
                return False
            return True
        definition = providers.get(candidate)
        if isinstance(definition, dict) and independent_api_auth(definition):
            return True
        if candidate == "openai":
            return None if auth_error is not None and quota_workspace is None else account_type == "apiKey"
        _LOG.info("Codex接続先を除外しました: provider=%s reason=undefined_or_no_independent_api_auth", candidate)
        return False

    available: list[str] = []
    for kind, candidate in entries:
        if candidate == primary:
            continue
        state = usable(kind, candidate)
        if state is None:
            if primary is None and not available:
                raise AppServerError("現在のCodex認証を確認できないため、接続先を選べない") from auth_error
            break
        if state:
            available.append(candidate)
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
    subscription = requires_subscription(selected) and account_type == "chatgpt"
    allowed = None
    usage_limit_type = None
    usage_limit_resets_at = None
    auto_reset_permitted = False
    if subscription:
        if usage is None:
            try:
                usage = await request("account/rateLimits/read", {})
            except RuntimeError, OSError:
                usage = {}

        active_id = quota_workspace or codex_fast_mode.workspace_id(auth)
        snapshot_id = usage.get("accountId")
        if snapshot_id is None or active_id is not None and snapshot_id == active_id:
            if quota_workspace is None and usage_refresh.enabled():
                await run_file_operation(
                    usage_refresh.record_codex,
                    usage,
                    account=usage_refresh.codex_account(auth),
                    permission_account=usage_refresh.codex_permission_account(auth),
                    contract=str(account.get("planType") or "") if isinstance(account, dict) else "",
                )
            value = usage.get("ordinaryUsageAllowed")
            allowed = value if isinstance(value, bool) else None
            limit = codex_usage_limit.from_rate_limits(usage.get("rateLimits"))
            usage_limit_type, usage_limit_resets_at = limit.limit_type, limit.resets_at
            if allowed is False and primary is None and quota_workspace is None:
                try:
                    permissions = await run_file_operation(codex_auto_reset.active_permissions)
                    if permissions:
                        root = await run_file_operation(state_paths.state_dir)
                        auto_reset_permitted = await run_file_operation(
                            codex_auto_reset.has_permission, permissions, account, snapshot_id, state_dir=root
                        )
                except OSError, ValueError:
                    _LOG.exception("Codex自動resetの許可を確認できません。設定と台帳を確認してください")
    return ProviderSelection(
        selected,
        subscription,
        tuple(remainder),
        allowed,
        usage_limit_type,
        tuple(diagnostics),
        usage_limit_resets_at,
        auto_reset_permitted,
        auth,
        quota_workspace,
    )


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
