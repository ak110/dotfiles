"""契約・接続・提供期間からCodexの自動速度を判定する。  pyfltr: allow-comment"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import hashlib
import json
import logging
import pathlib
import re
from collections.abc import Awaitable, Callable, Generator
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
    observed_at: datetime.datetime | None = dataclasses.field(default=None, compare=False, repr=False)
    allowed_until: datetime.datetime | None = dataclasses.field(default=None, compare=False, repr=False)
    identity: str | None = dataclasses.field(default=None, compare=False, repr=False)


def record_start_decision(instant: datetime.datetime, decision: FastTier) -> None:
    try:
        path = state_paths.state_dir() / "codex-fast-start.json"
        atomic_file.atomic_write(
            path,
            json.dumps(
                {
                    "at": instant.isoformat(),
                    "tier": decision.tier,
                    "explanation": decision.explanation,
                    "source": decision.source,
                }
            ),
        )
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


def _provider_key(provider: str) -> str:
    return hashlib.sha256(provider.encode()).hexdigest()


def _identity_key(workspace: str | None) -> str | None:
    return hashlib.sha256(workspace.encode()).hexdigest() if workspace is not None else None


@contextlib.contextmanager
def _decision_store() -> Generator[tuple[pathlib.Path, dict[str, Any]]]:
    root = state_paths.state_dir()
    path = root / "codex-fast-success.json"
    root.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(root / "codex-fast-success.lock"), timeout=0.2, is_singleton=True):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("Codexの成功判定が辞書ではない")
        yield path, loaded


@contextlib.contextmanager
def outgoing_decision(provider: str | None, decision: FastTier, instant: datetime.datetime) -> Generator[FastTier]:
    if decision.tier != "priority" or decision.observed_at is None:
        yield decision
        return
    unavailable = FastTier("default", "送信前に最新の高速判定を確認できない", "unavailable")
    selected = decision
    with contextlib.ExitStack() as stack:
        try:
            _, records = stack.enter_context(_decision_store())
        except OSError, ValueError, filelock.Timeout:
            selected = unavailable
        else:
            record = records.get(_provider_key(provider or "openai"))
            if decision.allowed_until is not None and instant >= decision.allowed_until:
                selected = FastTier("default", "送信前に高速判定の有効期限を過ぎた", "unavailable")
            elif record is not None and _valid_record(record, instant):
                current_at = datetime.datetime.fromisoformat(record["at"])
                if current_at > decision.observed_at and (
                    record.get("tier") != "priority"
                    or record.get("workspace") != decision.identity
                    or datetime.datetime.fromisoformat(record["until"]) <= instant
                ):
                    selected = FastTier("default", "送信前に新しい接続条件を確認")
        yield selected


def outgoing_metadata(provider: str | None, decision: FastTier, *, record_start: bool) -> dict[str, Any]:
    return {
        "provider": provider,
        "tier": decision.tier,
        "explanation": decision.explanation,
        "source": decision.source,
        "observed_at": decision.observed_at.isoformat() if decision.observed_at is not None else None,
        "allowed_until": decision.allowed_until.isoformat() if decision.allowed_until is not None else None,
        "identity": decision.identity,
        "record_start": record_start,
    }


@contextlib.contextmanager
def apply_outgoing_metadata(params: dict[str, Any], metadata: dict[str, Any]) -> Generator[None]:
    instant = datetime.datetime.now(datetime.UTC)
    observed, deadline = metadata["observed_at"], metadata["allowed_until"]
    decision = FastTier(
        metadata["tier"],
        metadata["explanation"],
        metadata["source"],
        datetime.datetime.fromisoformat(observed) if observed is not None else None,
        datetime.datetime.fromisoformat(deadline) if deadline is not None else None,
        metadata["identity"],
    )
    with outgoing_decision(metadata["provider"], decision, instant) as resolved:
        params["serviceTier"] = resolved.tier
        if metadata["record_start"]:
            record_start_decision(instant, resolved)
        yield


@contextlib.contextmanager
def apply_legacy_outgoing_metadata(params: dict[str, Any], metadata: dict[str, Any]) -> Generator[None]:
    if metadata["tier"] == "priority" and metadata["observed_at"] is not None:
        metadata = {
            **metadata,
            "tier": "default",
            "source": "unavailable",
            "explanation": "旧バージョンのApp Server仲介では送信直前の高速判定を確認できない",
        }
    with apply_outgoing_metadata(params, metadata):
        yield


def _cache_decision(
    provider: str | None,
    workspace: str | None,
    instant: datetime.datetime,
    decision: FastTier | None = None,
    deadline: datetime.datetime | None = None,
    *,
    read_at: datetime.datetime | None = None,
) -> FastTier | None:
    if provider is None:
        return None
    key = _provider_key(provider)
    identity = _identity_key(workspace)
    try:
        with _decision_store() as (path, loaded):
            records = {name: value for name, value in loaded.items() if _valid_record(value, instant)}
            retained, result = _transition(records.get(key), identity, instant, decision, deadline, read_at=read_at)
            if retained is None:
                records.pop(key, None)
            else:
                records[key] = retained
            if records != loaded:
                newest = sorted(
                    records.items(), key=lambda entry: datetime.datetime.fromisoformat(entry[1]["at"]), reverse=True
                )[:16]
                atomic_file.atomic_write(path, json.dumps(dict(newest)))
            return result
    except OSError, ValueError, filelock.Timeout:
        _LOG.debug("Codexの自動速度判定の成功値を読み書きできません", exc_info=True)
    return None


def _transition(
    previous: dict[str, Any] | None,
    identity: str | None,
    instant: datetime.datetime,
    decision: FastTier | None,
    deadline: datetime.datetime | None,
    *,
    read_at: datetime.datetime | None = None,
) -> tuple[dict[str, Any] | None, FastTier | None]:
    if previous is not None and not _valid_record(previous, instant):
        previous = None
    if previous is not None and datetime.datetime.fromisoformat(previous["at"]) > instant:
        return previous, None
    if decision is not None:
        if identity is None or deadline is None or instant >= deadline:
            return {"at": instant.isoformat()}, None
        return {
            "workspace": identity,
            "until": min(instant + _REUSE_LIMIT, deadline).isoformat(),
            "at": instant.isoformat(),
            "tier": decision.tier,
            "explanation": decision.explanation,
        }, None
    if previous is None or identity is None or "workspace" not in previous:
        return previous, None
    if previous["workspace"] != identity:
        return {"at": instant.isoformat()}, None
    if datetime.datetime.fromisoformat(previous["until"]) <= (read_at or instant):
        return previous, None
    return previous, FastTier(
        previous["tier"],
        previous["explanation"],
        "previous",
        datetime.datetime.fromisoformat(previous["at"]),
        datetime.datetime.fromisoformat(previous["until"]),
        identity,
    )


def _valid_record(record: Any, instant: datetime.datetime) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        at = datetime.datetime.fromisoformat(record["at"])
        if at.tzinfo is None or instant >= at + _REUSE_LIMIT:
            return False
        if set(record) == {"at"}:
            return True
        return (
            isinstance(record.get("workspace"), str)
            and record.get("tier") in ("priority", "default")
            and isinstance(record.get("explanation"), str)
            and datetime.datetime.fromisoformat(record["until"]).tzinfo is not None
        )
    except KeyError, TypeError, ValueError:
        return False


async def invalidate_provider(provider: str) -> None:
    await asyncio.to_thread(_cache_decision, provider, None, datetime.datetime.now(datetime.UTC), FastTier("default", ""))


def _account_observation(
    auth: dict[str, Any], instant: datetime.datetime
) -> tuple[FastTier | None, str | None, datetime.datetime | None]:
    account = auth.get("account")
    if ("account" in auth and account is None) or isinstance(account, dict) and account.get("type") == "apiKey":
        return FastTier("default", "ChatGPTアカウントで認証していない"), None, None
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        return None, None, None
    workspace = workspace_id(auth)
    active = [(plans, end) for plans, end, _source in _FREE_FAST_MODE_PERIODS if instant < end]
    if not active:
        return FastTier("default", "現在は提供期間外"), workspace, None
    plan = account.get("planType")
    if not isinstance(plan, str) or not re.fullmatch(r"[a-z_]{1,64}", plan):
        return None, workspace, None
    for plans, end in active:
        if plan in plans:
            return FastTier("priority", f"planType={plan}、提供終端={end.isoformat()}"), workspace, end
    return FastTier("default", f"planType={plan}は提供対象外"), workspace, min(end for _, end in active)


async def observe_selection_auth(providers: tuple[str, ...], auth: dict[str, Any], instant: datetime.datetime) -> None:
    observed, workspace, _deadline = _account_observation(auth, instant)
    for provider in providers:
        if observed is not None and (observed.tier == "default" or workspace is None):
            await asyncio.to_thread(_cache_decision, provider, workspace, instant, observed)
        elif workspace is not None:
            await asyncio.to_thread(_cache_decision, provider, workspace, instant)


async def auto_service_tier(
    request: _RPC,
    cwd: str,
    *,
    subscription: bool | None,
    now: datetime.datetime | None = None,
    provider: str | None = None,
    auth: dict[str, Any] | None = None,
    quota_workspace: str | None = None,
    auth_observed_at: datetime.datetime | None = None,
) -> FastTier:
    instant = now if now is not None else datetime.datetime.now(datetime.UTC)
    if subscription is False or not any(instant < end for _plans, end, _source in _FREE_FAST_MODE_PERIODS):
        decision = FastTier("default", "サブスクリプション接続ではない" if subscription is False else "現在は提供期間外")
        await asyncio.to_thread(_cache_decision, provider, None, instant, decision)
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
            instant = now or datetime.datetime.now(datetime.UTC)
            decision = FastTier("default", "サブスクリプション接続ではない")
            await asyncio.to_thread(_cache_decision, provider, None, instant, decision)
            return decision
        provider = current_provider

    workspace = quota_workspace
    workspace_at = auth_observed_at if quota_workspace is not None else None
    for attempt in range(0 if quota_workspace is not None else 2):
        if auth is None or attempt:
            try:
                async with asyncio.timeout(_RETRY_ATTEMPT_SECONDS):
                    auth = await request("account/read", {"refreshToken": False})
                auth_observed_at = now or datetime.datetime.now(datetime.UTC)
            except Exception:  # pylint: disable=broad-exception-caught
                continue
        instant = now if now is not None else datetime.datetime.now(datetime.UTC)
        observed_at = auth_observed_at or instant
        decision, current_workspace, deadline = _account_observation(auth, instant)
        if decision is not None:
            await asyncio.to_thread(_cache_decision, provider, current_workspace, observed_at, decision, deadline)
            return dataclasses.replace(
                decision, observed_at=observed_at, allowed_until=deadline, identity=_identity_key(current_workspace)
            )
        if current_workspace is not None:
            workspace = current_workspace
            workspace_at = observed_at
    if workspace is None and provider is not None:
        workspace = quota_workspace_id(await read_workspace_quota(request))
        workspace_at = now or datetime.datetime.now(datetime.UTC)
    instant = now if now is not None else datetime.datetime.now(datetime.UTC)
    if not any(instant < end for _plans, end, _source in _FREE_FAST_MODE_PERIODS):
        decision = FastTier("default", "現在は提供期間外")
        await asyncio.to_thread(_cache_decision, provider, workspace, instant, decision)
        return decision
    previous = await asyncio.to_thread(_cache_decision, provider, workspace, workspace_at or instant, read_at=instant)
    return previous or FastTier(
        "default",
        "契約を取得できず、再利用できる判定がない"
        if workspace and provider
        else "認証済みワークスペースと実効接続の同一性を確認できない",
        "unavailable",
    )
