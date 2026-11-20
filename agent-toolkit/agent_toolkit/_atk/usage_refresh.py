"""公開された利用枠の読取とSDK通知を、同じ観測形式へ変換する。"""

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
from agent_toolkit._common import claude_usage_limit, codex_models, json_lines, process_tree, usage_observation, usage_settings
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


def enabled(*, force: bool = False) -> bool:
    now = time.monotonic()
    if force or now - _CLOCK.checked_at >= 60:
        try:
            value = config.resolve_mutable_setting("usage_auto_allocation") == "true"
        except (OSError, ValueError, KeyError) as error:
            _LOG.warning("利用枠の観測設定を読めません。config get usage_auto_allocationで確認してください: %s", error)
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
def cli_activity(engine: str, model: str | None, effort: str | None, connection: str | None = None) -> Generator[None]:
    """CLIの実行区間だけを計測し、解除待ちや停止中の時間と区別する。"""
    if engine not in {"claude", "codex"} or not enabled():
        yield
        return
    identifier = f"cli:{os.getpid()}"
    fast = engine == "codex" and config.resolve_mutable_setting("codex_fast_mode") == "true"
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
    # アカウントの枠は別端末・製品でも消費されるため、設定の因果効果とは扱わない。
    try:
        usage_observation.observe(windows, setting_signature=signature, active_seconds=seconds, mixed=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _LOG.warning("利用枠の観測を保存できません。config usageで取得状態を確認してください: %s", error)


def record_codex(raw: dict[str, Any], *, account: str = "unknown", contract: str = "") -> None:
    now = time.time()
    previous = usage_settings.read_metadata("codex")
    if account == "unknown":
        account = previous.get("account", "unknown")
    account = str(raw.get("accountId") or account)
    if not contract and previous.get("account") == account:
        contract = previous.get("contract", "")
    contract = contract if contract and not contract.startswith("unknown") else f"unknown:{now}"
    by_id = raw.get("rateLimitsByLimitId")
    buckets = by_id if isinstance(by_id, dict) else {"codex": raw.get("rateLimits")}
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
                    str(raw.get("accountId") or account),
                    f"{name}:{label}",
                    _number(window.get("usedPercent"), 100),
                    _timestamp(window.get("resetsAt")),
                    now,
                    scope="all" if name == "codex" else str(bucket.get("limitName") or name),
                    contract=contract,
                    duration_seconds=minutes * 60
                    if (minutes := _number(window.get("windowDurationMins"))) is not None
                    else None,
                )
            )
    _store(windows)
    inventory = raw.get("rateLimitResetCredits")
    usage_settings.record_metadata(
        "codex",
        {
            "account": str(raw.get("accountId") or account),
            "contract": contract,
            "reset_available_count": _number(inventory.get("availableCount")) if isinstance(inventory, dict) else None,
        },
    )


def record_claude_report(report: dict[str, Any], *, account: str | None = None, contract: str | None = None) -> None:
    """SDKUsageReportのpercent（0–100）を扱い、API費用のsession欄は残量へ換算しない。"""
    limits = report.get("rate_limits")
    rows = limits.get("limits") if isinstance(limits, dict) else None
    if not isinstance(rows, list):
        return
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
                "extra_usage_limit": _number(extra.get("monthly_limit")),
                "currency": str(extra.get("currency", "unknown"))[:16],
                "amount_unit": "通貨の最小単位。USDはセント",
                "note": "追加支出の表示。サブスクリプション残量やトークンのAPI換算額とは別の値",
            },
        )


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
    scope = "model:" + kind.removeprefix("seven_day_") if kind in {"seven_day_opus", "seven_day_sonnet"} else "all"
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
    account = auth.get("account")
    email = account.get("email") if isinstance(account, dict) else None
    if isinstance(email, str) and email:
        return hashlib.sha256(email.encode()).hexdigest()
    return f"unknown:{time.time()}"


async def _codex_usage(request: Callable[..., Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    auth = await request("account/read", {"refreshToken": False})
    account = auth.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise ValueError("Codexのサブスクリプションアカウントを確認できない")
    raw = await request("account/rateLimits/read", {})
    record_codex(raw, account=codex_account(auth), contract=str(account.get("planType", "")))
    return raw


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
        found = False
        for line in json_lines.split_lines(result.stdout):
            with contextlib.suppress(json.JSONDecodeError):
                event = json.loads(line)
                report = event.get("usage_report") if isinstance(event, dict) else None
                if report is None and isinstance(event, dict) and isinstance(event.get("message"), dict):
                    report = event["message"].get("usage_report")
                if isinstance(report, dict):
                    record_claude_report(report, account=identity, contract=contract)
                    found = True
        if not found:
            raise ValueError("/usageから構造化した利用枠を取得できない")
    except (OSError, ValueError, StopIteration, subprocess.TimeoutExpired) as error:
        errors.append(f"Claude: {type(error).__name__}: {error}")
    if not quiet:
        for error in errors:
            print(f"利用枠の取得失敗（過去の観測は保持）: {error}")
    return errors


def ensure_fresh(*, force: bool = False, environment: Mapping[str, str] | None = None) -> None:
    """起動境界で5分の共有キャッシュを使い、同時照会は1主体へ集約する。"""
    automatic = enabled(force=True)
    if not force and not automatic:
        return
    path = usage_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".refresh.lock").open("a+", encoding="utf-8") as lock:
        try:
            acquire_lock(lock, blocking=False)
        except OSError:
            return
        try:
            state = usage_settings.read_settings()
            previous = state.get("last_refresh_at")
            if (
                not force
                and state.get("refresh_context") == usage_settings.context_id()
                and isinstance(previous, (int, float))
                and time.time() - previous < 300
            ):
                return
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
        finally:
            release_lock(lock)
