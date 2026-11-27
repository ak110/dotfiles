from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import logging
import math
import os
import subprocess
import time
from collections.abc import Awaitable, Callable, Generator, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_toolkit._atk import claude_connection, config
from agent_toolkit._common import claude_connection as claude_settings
from agent_toolkit._common import (
    claude_usage_limit,
    codex_models,
    codex_weekly,
    json_lines,
    process_tree,
    usage_observation,
    usage_settings,
)
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock

_ACTIVE: dict[str, tuple[str, str]] = {}
_SECONDS: dict[str, float] = {}


@dataclass
class _ActivityClock:
    tick: float = field(default_factory=time.monotonic)
    checked_at: float = 0.0
    enabled: bool = False
    finishing: dict[str, str] = field(default_factory=dict)


_CLOCK = _ActivityClock()
_LOG = logging.getLogger("agent-toolkit.usage")
DISPLAY_REFRESH_INTERVAL_SEC = 300


def enabled(*, force: bool = False) -> bool:
    now = time.monotonic()
    if force or now - _CLOCK.checked_at >= 60:
        try:
            value = bool(json.loads(config.resolve_mutable_setting("usage_reset_permissions")))
        except (OSError, ValueError, KeyError) as error:
            _LOG.warning("利用枠の観測設定を読めません。config get usage_reset_permissionsで確認してください: %s", error)
            value = False
        _CLOCK.checked_at = now
        _CLOCK.enabled = value
        if not value:
            _ACTIVE.clear()
            _SECONDS.clear()
            _CLOCK.tick = now
    return _CLOCK.enabled


def _tick() -> None:
    now = time.monotonic()
    for engine in {value[0] for value in _ACTIVE.values()}:
        _SECONDS[engine] = _SECONDS.get(engine, 0.0) + now - _CLOCK.tick
    _CLOCK.tick = now


def track_session(session: Any) -> None:
    """稼働区間をengineごとの和集合で数え、終了・上限待機・背景待機を除く。"""
    if not enabled():
        return
    _tick()
    active = (
        session.status == "running"
        and session.cli_turn_state not in {"idle", "requires_action"}
        and session.usage_limit_first_at is None
        and not session.awaiting_auto_resume
    )
    if active:
        signature = session_signature(session)
        _ACTIVE[session.session_id] = (session.engine, signature)
    else:
        _ACTIVE.pop(session.session_id, None)


def session_signature(session: Any) -> str:
    return json.dumps(
        [
            session.engine,
            session.model,
            session.effort,
            session.fast_mode,
            session.claude_connection,
            session.codex_model_provider,
        ]
    )


@contextlib.contextmanager
def cli_activity(
    engine: str,
    model: str | None,
    effort: str | None,
    connection: str | None = None,
    *,
    service_tier: str | None = None,
) -> Generator[None]:
    if engine not in {"claude", "codex"} or not enabled():
        yield
        return
    identifier = f"cli:{os.getpid()}"
    # CLIが速度を送っていない場合は、設定値から実効速度を推定しない。  # pyfltr: allow-comment
    fast = (service_tier == "priority" if service_tier is not None else None) if engine == "codex" else False
    signature = json.dumps(
        [engine, model, effort, fast, connection if engine == "claude" else None, connection if engine == "codex" else None]
    )
    _tick()
    _ACTIVE[identifier] = (engine, signature)
    try:
        yield
    finally:
        _tick()
        _ACTIVE.pop(identifier, None)
        _CLOCK.finishing[engine] = signature
        try:
            ensure_fresh()
        except (OSError, ValueError, RuntimeError) as error:
            _LOG.warning("CLI終了時の利用枠を取得できません。config usage --refreshで再取得してください: %s", error)
        finally:
            _CLOCK.finishing.pop(engine, None)


def _number(value: Any, maximum: float = float("inf")) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= maximum
        else None
    )


def _timestamp(value: Any) -> float | None:
    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value)
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except ValueError:
            return None
    return _number(value)


def _store(windows: list[usage_observation.QuotaWindow], signature: str = "unattributed") -> None:
    if not windows:
        return
    _tick()
    engine = windows[0].engine
    seconds = _SECONDS.get(engine, 0.0)
    signatures = {value[1] for value in _ACTIVE.values() if value[0] == engine}
    if len(signatures) == 1:
        signature = next(iter(signatures))
    elif not signatures:
        signature = _CLOCK.finishing.get(engine, signature)

    try:
        usage_observation.observe(windows, setting_signature=signature, active_seconds=seconds, mixed=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _LOG.warning("利用枠の観測を保存できません。config usageで取得状態を確認してください: %s", error)


def record_codex(raw: dict[str, Any], *, account: str = "unknown", contract: str = "", permission_account: str = "") -> None:
    now = time.time()
    previous = usage_settings.read_metadata("codex")
    routing = raw.get("workspaceRouting")
    routed = routing.get("chatgptAccountId") if isinstance(routing, dict) else None
    supplied = raw.get("accountId") or routed or account
    identified = supplied if isinstance(supplied, str) and supplied != "unknown" and not supplied.startswith("unknown:") else ""
    account = str(supplied if supplied != "unknown" else previous.get("account", "unknown"))
    by_id = raw.get("rateLimitsByLimitId")
    buckets = by_id if isinstance(by_id, dict) else {"codex": raw.get("rateLimits")}
    default_bucket = buckets.get("codex")
    if not isinstance(default_bucket, dict):
        default_bucket = {}
    contract = contract or _text(default_bucket.get("planType")) or ""
    if not contract and previous.get("account") == account:
        contract = previous.get("contract", "")
    contract = contract if contract and not contract.startswith("unknown") else f"unknown:{now}"
    windows = []
    for name, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        for label, window in bucket.items():
            if not isinstance(window, dict) or "usedPercent" not in window:
                continue
            windows.append(
                usage_observation.QuotaWindow(
                    "codex",
                    account,
                    f"{name}:{label}",
                    _number(window.get("usedPercent"), 100),
                    _timestamp(window.get("resetsAt")),
                    now,
                    scope="all" if name == "codex" else str(bucket.get("limitName") or name),
                    contract=contract,
                    limit_name=_text(bucket.get("limitName")) or _text(bucket.get("limitId")) or str(name),
                    duration_seconds=minutes * 60
                    if (minutes := _number(window.get("windowDurationMins"))) is not None
                    else None,
                )
            )
    _store(windows)
    inventory = raw.get("rateLimitResetCredits")
    additional_usage = default_bucket.get("credits", raw.get("credits"))
    details = inventory.get("credits") if isinstance(inventory, dict) else None
    weekly_window = None
    with contextlib.suppress(ValueError):
        weekly = codex_weekly.select_weekly(raw)
        if weekly.window is not None:
            weekly_window = {"window_id": weekly.window_id, "resets_at": weekly.window.resetsAt}
    usage_settings.record_metadata(
        "codex",
        {
            "account": account,
            "contract": contract,
            "permission_account": permission_account,
            "weekly_window": weekly_window,
            "ordinaryUsageAllowed": _boolean(raw.get("ordinaryUsageAllowed")),
            "spendControlReached": _boolean(default_bucket.get("spendControlReached", raw.get("spendControlReached"))),
            "rateLimitReachedType": _text(default_bucket.get("rateLimitReachedType", raw.get("rateLimitReachedType"))),
            "credits": {
                "hasCredits": _boolean(additional_usage.get("hasCredits")),
                "unlimited": _boolean(additional_usage.get("unlimited")),
                "balance": _text(additional_usage.get("balance")),
            }
            if isinstance(additional_usage, dict)
            else None,
            "reset_available_count": _number(inventory.get("availableCount")) if isinstance(inventory, dict) else None,
            "reset_credit_details": [
                {"resetType": _text(item.get("resetType")), "status": _text(item.get("status"))}
                for item in details
                if isinstance(item, dict)
            ]
            if isinstance(details, list)
            else None,
        },
        identified_account=identified,
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _boolean(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def record_claude_report(
    report: dict[str, Any], *, account: str | None = None, contract: str | None = None
) -> list[usage_observation.QuotaWindow]:
    limits = report.get("rate_limits")
    rows = limits.get("limits") if isinstance(limits, dict) else None
    if not isinstance(rows, list):
        return []
    now = time.time()
    profile = usage_settings.read_metadata("claude")
    account = str(account or profile.get("account") or "local-claude")
    contract = contract or profile.get("contract", "")
    contract = contract if contract and not contract.startswith("unknown") else f"unknown:{now}"
    windows = [
        usage_observation.QuotaWindow(
            "claude",
            account,
            str(row.get("kind", index)),
            _number(row.get("percent"), 100),
            _timestamp(row.get("resets_at")),
            now,
            scope=json.dumps(row["scope"], sort_keys=True) if row.get("scope") else "all",
            contract=contract,
            group=_text(row.get("group")),
            severity=_text(row.get("severity")),
            is_active=_boolean(row.get("is_active")),
        )
        for index, row in enumerate(rows)
        if isinstance(row, dict)
    ]
    _store(windows)
    assert isinstance(limits, dict)
    extra = limits.get("extra_usage")
    if isinstance(extra, dict):
        usage_settings.record_metadata(
            "claude",
            {
                "extra_usage_enabled": extra.get("is_enabled") if isinstance(extra.get("is_enabled"), bool) else None,
                "extra_usage_spend": _number(extra.get("used_credits")),
                "extra_usage_utilization": _number(extra.get("utilization")),
                "extra_usage_limit": _number(extra.get("monthly_limit")),
                "currency": str(extra.get("currency", "unknown"))[:16],
                "amount_unit": "通貨の最小単位。USDはセント",
                "note": "追加支出の表示。サブスクリプション残量やトークンのAPI換算額とは別の値",
            },
        )

    return windows


def record_claude_event(info: Any) -> None:
    if info is None or not enabled():
        return
    values = (
        info
        if isinstance(info, dict)
        else {key: getattr(info, key, None) for key in ("utilization", "rate_limit_type", "resets_at")}
    )
    limit = claude_usage_limit.from_info(info)
    utilization = _number(values.get("utilization"), 1)
    kind = values.get("rateLimitType", values.get("rate_limit_type"))
    if not isinstance(kind, str) or kind == "overage":
        return
    _report_kind, scope = claude_usage_limit.REPORT_WINDOWS.get(kind, (kind, "all"))
    profile = usage_settings.read_metadata("claude")
    contract = str(profile.get("contract", ""))
    if not contract or contract.startswith("unknown"):
        contract = f"unknown:{time.time()}"
    _store(
        [
            usage_observation.QuotaWindow(
                "claude",
                profile.get("account", "local-claude"),
                kind,
                utilization * 100 if utilization is not None else None,
                _timestamp(values.get("resetsAt", values.get("resets_at"))),
                time.time(),
                scope=scope,
                contract=contract,
                rejected=limit is not None and limit.is_wait_target,
            )
        ]
    )


def codex_account(auth: dict[str, Any]) -> str:
    """読取と通知の補完に同じ認証済みアカウント識別子を使う。"""
    routing = auth.get("workspaceRouting")
    if isinstance(routing, dict) and routing.get("chatgptAccountId"):
        return str(routing["chatgptAccountId"])
    return codex_permission_account(auth) or f"unknown:{time.time()}"


def codex_permission_account(auth: dict[str, Any]) -> str:
    account = auth.get("account")
    email = account.get("email") if isinstance(account, dict) else None
    return hashlib.sha256(email.encode("utf-8")).hexdigest() if isinstance(email, str) and email.strip() else ""


async def _codex_usage(request: Callable[..., Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    auth = await request("account/read", {"refreshToken": False})
    account = auth.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise ValueError("Codexのサブスクリプションアカウントを確認できない")
    raw = await request("account/rateLimits/read", {})
    record_codex(
        raw,
        account=codex_account(auth),
        contract=str(account.get("planType", "")),
        permission_account=codex_permission_account(auth),
    )
    return raw


def refresh_claude(
    selected: claude_connection.Connection,
    environment: Mapping[str, str] | None,
) -> list[usage_observation.QuotaWindow]:
    environment = claude_connection.connection_environment(os.environ if environment is None else environment, selected)
    settings = claude_settings.merge_settings(
        None, {}, None, isolate_auth=selected.kind != "inherit", isolation_keys=claude_connection.isolation_keys(selected)
    )
    auth = process_tree.run_with_timeout(
        ["claude", "--settings", settings, "auth", "status"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if auth.returncode != 0:
        raise ValueError(
            f"auth statusが終了{auth.returncode}で失敗した。"
            "claude auth statusで認証状態を確認し、atk config usage --refreshで再取得してください"
        )
    profile = json.loads(auth.stdout)
    if not isinstance(profile, dict) or not profile.get("email") or profile.get("loggedIn") is False:
        raise ValueError(
            "auth statusから認証済みアカウントを確認できない。"
            "claude auth statusで認証状態を確認し、atk config usage --refreshで再取得してください"
        )
    identity = hashlib.sha256(
        json.dumps([profile.get("email"), profile.get("organization"), environment.get("CLAUDE_CONFIG_DIR")]).encode()
    ).hexdigest()
    contract = str(profile.get("subscriptionType") or f"unknown:{time.time()}")
    usage_settings.record_metadata("claude", {"account": identity, "contract": contract})
    result = process_tree.run_with_timeout(
        [
            "claude",
            "--settings",
            settings,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--output-format",
            "stream-json",
            "--verbose",
            "--",
            "/usage",
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"/usageが終了{result.returncode}で失敗した")
    windows = None
    for line in json_lines.split_lines(result.stdout):
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(line)
            report = event.get("usage_report") if isinstance(event, dict) else None
            if report is None and isinstance(event, dict) and isinstance(event.get("message"), dict):
                report = event["message"].get("usage_report")
            if isinstance(report, dict):
                windows = record_claude_report(report, account=identity, contract=contract)
    if windows is None:
        raise ValueError("/usageから構造化した利用枠を取得できない")
    return windows


def refresh(*, quiet: bool = False, environment: Mapping[str, str] | None = None) -> list[str]:
    """turnを起動しない読取だけを行い、片側の失敗でも取得できた側を保持する。"""
    errors = []
    try:
        asyncio.run(codex_models.with_app_server(_codex_usage))
    except (OSError, RuntimeError, ValueError) as error:
        errors.append(f"Codex: {type(error).__name__}: {error}")
    try:
        selected = next(
            item
            for item in claude_connection.candidates("delegate", env=environment).connections
            if item.kind in {"subscription", "inherit"}
        )
        refresh_claude(selected, environment)
    except (OSError, ValueError, StopIteration, subprocess.TimeoutExpired) as error:
        errors.append(f"Claude: {type(error).__name__}: {error}")
    if not quiet:
        for error in errors:
            print(f"利用枠の取得失敗（過去の観測は保持）: {error}")
    return errors


def ensure_fresh(*, force: bool = False, environment: Mapping[str, str] | None = None) -> None:
    automatic = enabled(force=True)
    if not force and not automatic:
        return
    _refresh_locked(force=force, environment=environment)


def refresh_for_display(*, force: bool = False) -> dict[str, Any]:
    return _refresh_locked(force=force, requested_at=time.time())


def _refresh_locked(
    *, force: bool, environment: Mapping[str, str] | None = None, requested_at: float | None = None
) -> dict[str, Any]:
    path = usage_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".refresh.lock").open("a+", encoding="utf-8") as lock:
        try:
            acquire_lock(lock, blocking=requested_at is not None)
        except OSError:
            if requested_at is not None:
                raise
            return {}
        try:
            state = usage_settings.read_settings()
            previous = state.get("last_refresh_at")
            interval = 1 if requested_at is not None and state.get("refresh_failures") else DISPLAY_REFRESH_INTERVAL_SEC
            if (
                state.get("refresh_context") == usage_settings.context_id()
                and isinstance(previous, (int, float))
                and (
                    (not force and time.time() - previous < interval) or (requested_at is not None and previous >= requested_at)
                )
            ):
                return state
            errors = refresh(quiet=True, environment=environment)
            with path.with_suffix(".lock").open("a+", encoding="utf-8") as settings_lock:
                acquire_lock(settings_lock)
                try:
                    state = usage_settings.read_settings()
                    state.update(
                        last_refresh_at=time.time(),
                        refresh_failures=errors,
                        refresh_context=usage_settings.context_id(),
                    )
                    atomic_write(path, json.dumps(state, ensure_ascii=False) + "\n")
                finally:
                    release_lock(settings_lock)
            return state
        finally:
            release_lock(lock)
