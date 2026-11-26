"""契約・接続・提供期間からCodexの自動速度を判定する。  pyfltr: allow-comment"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import filelock

from agent_toolkit._common import atomic_file, state_paths

_LOG = logging.getLogger(__name__)


_FREE_FAST_MODE_PERIODS = (
    (
        frozenset({"business", "team"}),
        datetime.datetime.fromisoformat("2027-01-01T00:00:00+09:00"),
        "https://x.com/thsottiaux/status/2125299248782778588",
    ),
)
"""提供条件を変える場合は、対象のplanType、時差付き終端、根拠URLを同じ行に記録する。  pyfltr: allow-comment"""


@dataclasses.dataclass(frozen=True)
class FastTier:
    tier: str
    explanation: str
    source: Literal["current", "previous", "unavailable", "legacy"] = "current"


def record_start_decision(instant: datetime.datetime, decision: FastTier) -> None:
    try:
        path = state_paths.state_dir() / "codex-fast-start.json"
        atomic_file.atomic_write(path, json.dumps({"at": instant.isoformat(), **dataclasses.asdict(decision)}))
    except OSError:
        _LOG.debug("Codexの起動時判定を保存できません", exc_info=True)


def latest_start_decision() -> tuple[str, FastTier] | None:
    try:
        path = state_paths.state_dir() / "codex-fast-start.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("tier") not in ("priority", "default"):
            return None
        instant, explanation = record.get("at"), record.get("explanation")
        if not isinstance(instant, str) or not isinstance(explanation, str):
            return None
        if datetime.datetime.fromisoformat(instant).tzinfo is None:
            return None
        source = record.get("source")
        decision = FastTier(record["tier"], explanation, "legacy")
        if source == "current":
            decision = dataclasses.replace(decision, source="current")
        elif source == "previous":
            decision = dataclasses.replace(decision, source="previous")
        elif source == "unavailable":
            decision = dataclasses.replace(decision, source="unavailable")
        return instant, decision
    except OSError, ValueError:
        return None


_RETRY_ATTEMPT_SECONDS = 1.5
_REUSE_LIMIT = datetime.timedelta(minutes=5)
_RPC = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def read_with_retry(request: _RPC, method: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        async with asyncio.timeout(_RETRY_ATTEMPT_SECONDS):
            return await request(method, params)
    except Exception:  # pylint: disable=broad-exception-caught
        async with asyncio.timeout(_RETRY_ATTEMPT_SECONDS):
            return await request(method, params)


def workspace_id(auth: dict[str, Any]) -> str | None:
    routing = auth.get("workspaceRouting")
    value = routing.get("chatgptAccountId") if isinstance(routing, dict) else None
    return value if isinstance(value, str) and value else None


async def read_workspace_quota(request: _RPC) -> dict[str, Any]:
    try:
        async with asyncio.timeout(_RETRY_ATTEMPT_SECONDS):
            return await request("account/rateLimits/read", {})
    except Exception:  # pylint: disable=broad-exception-caught
        return {}


def quota_workspace_id(quota: dict[str, Any]) -> str | None:
    value = quota.get("accountId")
    return value if isinstance(value, str) and value else None


def _cache_decision(
    provider: str | None,
    workspace: str | None,
    instant: datetime.datetime,
    decision: FastTier | None = None,
    deadline: datetime.datetime | None = None,
    *,
    clear: bool = False,
) -> FastTier | None:
    if provider is None:
        return None
    key = hashlib.sha256(provider.encode()).hexdigest()
    identity = hashlib.sha256(workspace.encode()).hexdigest() if workspace is not None else None
    try:
        root = state_paths.state_dir()
        path = root / "codex-fast-success.json"
        root.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(str(root / "codex-fast-success.lock"), timeout=0.2, is_singleton=True):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError, ValueError:
                loaded = {}
            records = {}
            if isinstance(loaded, dict):
                for name, record in loaded.items():
                    if _valid_record(record, instant):
                        records[name] = record
            previous = records.get(key)
            if previous is not None and datetime.datetime.fromisoformat(previous["at"]) > instant:
                return None
            if clear or identity is not None and previous is not None and previous["workspace"] != identity:
                records.pop(key, None)
                previous = None
            if decision is not None and identity is not None and deadline is not None:
                records[key] = {
                    "workspace": identity,
                    "until": min(instant + _REUSE_LIMIT, deadline).isoformat(),
                    "at": instant.isoformat(),
                    "tier": decision.tier,
                    "explanation": decision.explanation,
                }
            if records != loaded:
                newest = sorted(
                    records.items(), key=lambda entry: datetime.datetime.fromisoformat(entry[1]["at"]), reverse=True
                )[:16]
                atomic_file.atomic_write(path, json.dumps(dict(newest)))
            if (
                decision is None
                and identity is not None
                and previous is not None
                and datetime.datetime.fromisoformat(previous["at"]) <= instant
            ):
                return FastTier(previous["tier"], previous["explanation"], "previous")
    except OSError, filelock.Timeout:
        _LOG.debug("Codexの自動速度判定の成功値を読み書きできません", exc_info=True)
    return None


def _valid_record(record: Any, instant: datetime.datetime) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        return (
            isinstance(record.get("workspace"), str)
            and record.get("tier") in ("priority", "default")
            and isinstance(record.get("explanation"), str)
            and datetime.datetime.fromisoformat(record["at"]).tzinfo is not None
            and instant < datetime.datetime.fromisoformat(record["until"])
        )
    except KeyError, TypeError, ValueError:
        return False


async def invalidate_provider(provider: str) -> None:
    await asyncio.to_thread(_cache_decision, provider, None, datetime.datetime.now(datetime.UTC), clear=True)


async def auto_service_tier(
    request: _RPC,
    cwd: str,
    *,
    subscription: bool | None,
    now: datetime.datetime | None = None,
    provider: str | None = None,
    auth: dict[str, Any] | None = None,
    quota_workspace: str | None = None,
) -> FastTier:
    instant = now if now is not None else datetime.datetime.now(datetime.UTC)
    active = tuple((plans, expires) for plans, expires, _source in _FREE_FAST_MODE_PERIODS if instant < expires)
    if not active or subscription is False:
        decision = FastTier("default", "現在は提供期間外" if not active else "サブスクリプション接続ではない")
        return decision
    if subscription is None:
        try:
            response = await read_with_retry(request, "config/read", {"cwd": cwd, "includeLayers": False})
        except Exception:  # pylint: disable=broad-exception-caught
            return FastTier("default", "接続情報を取得できない", "unavailable")
        config = response.get("config")
        if not isinstance(config, dict) or not isinstance(config.get("model_provider"), str):
            return FastTier("default", "実効接続を確認できない", "unavailable")
        current_provider = config["model_provider"]
        if current_provider != "openai" or provider not in (None, current_provider):
            return FastTier("default", "サブスクリプション接続ではない")
        provider = current_provider

    workspace = quota_workspace
    plan = None
    for attempt in range(0 if quota_workspace is not None else 2):
        if auth is None or attempt:
            try:
                async with asyncio.timeout(_RETRY_ATTEMPT_SECONDS):
                    auth = await request("account/read", {"refreshToken": False})
            except Exception:  # pylint: disable=broad-exception-caught
                continue
        if "account" not in auth:
            continue
        account = auth["account"]
        if account is None or isinstance(account, dict) and account.get("type") == "apiKey":
            await asyncio.to_thread(_cache_decision, provider, None, instant, clear=True)
            return FastTier("default", "ChatGPTアカウントで認証していない")
        if isinstance(account, dict) and account.get("type") == "chatgpt":
            workspace = workspace_id(auth)
            value = account.get("planType")
            if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,64}", value):
                plan = value
                break
    if workspace is None and plan is None and provider is not None:
        workspace = quota_workspace_id(await read_workspace_quota(request))

    instant = now if now is not None else datetime.datetime.now(datetime.UTC)
    active = tuple((plans, expires) for plans, expires in active if instant < expires)
    if not active:
        decision = FastTier("default", "現在は提供期間外")
        return decision
    if plan is None:
        previous = await asyncio.to_thread(_cache_decision, provider, workspace, instant)
        return previous or FastTier(
            "default",
            "契約を取得できず、再利用できる判定がない"
            if workspace and provider
            else "認証済みワークスペースと実効接続の同一性を確認できない",
            "unavailable",
        )
    for plans, expires in active:
        if plan in plans:
            decision = FastTier("priority", f"planType={plan}、提供終端={expires.isoformat()}")
            await asyncio.to_thread(_cache_decision, provider, workspace, instant, decision, expires)
            return decision
    decision = FastTier("default", f"planType={plan}は提供対象外")
    await asyncio.to_thread(_cache_decision, provider, workspace, instant, decision, min(expires for _, expires in active))
    return decision
