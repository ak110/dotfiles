"""利用枠の確認と、通常設定を変更しない期限付きの一時指定。"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import time
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk import claude_connection, config_settings, help_text, usage_allocation
from agent_toolkit._common import registry_index, session_registry, usage_observation
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.usage_settings import deadline, read_metadata, read_settings, settings_path, temporary_settings


def allowed_temporary_key(key: str) -> bool:
    return key in {
        *config_settings.MODEL_DEFAULTS,
        *config_settings.CONNECTION_ORDER_KEYS,
        "codex_fast_mode",
        "claude_usage_limit_wait",
        "codex_usage_limit_wait",
    }


def update_temporary(key: str | None, value: str | None, until: str | None) -> None:
    if key is not None:
        if not allowed_temporary_key(key) or value is None:
            raise ValueError("一時指定できるのは工程モデル・接続使用順・速度・上限待機方針だけ")
        config_settings.validate_mutable_setting(key, value)
    if until is not None:
        deadline(until)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+", encoding="utf-8") as lock:
        acquire_lock(lock)
        try:
            state = read_settings()
            if key is None:
                state.pop("values", None)
                state.pop("until", None)
            else:
                assert value is not None
                values = temporary_settings()
                values[key] = config_settings.normalize_setting(key, value)
                state.update(values=values, until=until)
            atomic_write(path, json.dumps(state, ensure_ascii=False) + "\n")
        finally:
            release_lock(lock)


def _profile_windows(observed: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = {engine: read_metadata(engine) for engine in ("claude", "codex")}
    result = []
    for window in observed.get("windows", []):
        profile = profiles.get(window["engine"], {})
        contract = profile.get("contract", "")
        if profile.get("account") != window["account"]:
            continue
        if contract and not contract.startswith("unknown") and contract != window["contract"]:
            continue
        result.append(window)
    return result


def select_candidates(
    candidates: list[tuple[str, str, str]],
    role: str,
    *,
    automatic: bool,
    activity_mode: str,
    excluded: dict[tuple[str, str, str], str] | None = None,
) -> tuple[list[tuple[str, str, str]], str]:
    if not automatic:
        return candidates, "自動配分は無効。通常設定と明示指定を使用する"
    temporary = temporary_settings()
    connection_key = config_settings.connection_order_key("claude", "orchestrator" if role == "orchestrate" else "delegate")
    connections = config_settings.parse_connection_order(
        os.environ.get(config_settings.config_env_name(connection_key))
        or temporary.get(connection_key)
        or config_settings.raw_mutable_setting(connection_key)
    )
    if "payg" in connections:
        return candidates, "明示した追加課金接続を優先し、サブスクリプション残量で候補を並べ替えない"
    observed: dict[str, Any]
    try:
        observed = usage_observation.snapshot()
    except OSError, ValueError, KeyError, TypeError:
        observed = {"windows": [], "rates": {}, "mixed": True}
    windows = [usage_observation.QuotaWindow(**item) for item in _profile_windows(observed)]
    windows = [
        window
        for window in windows
        if time.time() - window.observed_at <= 600 and (window.resets_at is None or window.resets_at > time.time())
    ]
    activity = 1.0 if activity_mode == "continuous" else observed.get("activity_ratio", 1.0)
    if not isinstance(activity, (int, float)) or not math.isfinite(activity):
        activity = 1.0
    rates: dict[str, float] = {}
    for signature, window_rates in observed.get("setting_rates", {}).items():
        try:
            setting = json.loads(signature)
        except json.JSONDecodeError:
            continue
        if isinstance(setting, list) and len(setting) >= 3 and tuple(setting[:3]) in candidates:
            for key, rate in window_rates.items():
                rates[key] = max(rates.get(key, 0), rate)
    state = read_settings()
    decisions = state.get("decisions", {})
    previous = decisions.get(role, {}).get("engine") if isinstance(decisions, dict) else None
    has_credits = (
        role != "orchestrate"
        and "credits" in connections
        and any(window.claude_limit_reason for window in windows)
        and any(item.kind == "credits" for item in claude_connection.candidates("delegate").connections)
    )
    selected, reason = usage_allocation.choose(
        candidates,
        role=role,
        windows=windows,
        rates=rates,
        activity_ratio=activity,
        previous=previous,
        excluded=excluded,
        subscription_only=not has_credits,
    )
    if selected and role != "orchestrate" and selected[0][0] != previous:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a+", encoding="utf-8") as lock:
            acquire_lock(lock)
            try:
                state = read_settings()
                state.setdefault("decisions", {})[role] = {"engine": selected[0][0], "reason": reason, "at": time.time()}
                atomic_write(path, json.dumps(state, ensure_ascii=False) + "\n")
            finally:
                release_lock(lock)
    return selected, reason


def build_parser(sub: Any) -> None:
    usage = help_text.add_command(
        sub,
        "usage",
        **help_text.HELP["atk config usage"],
    )
    usage.add_argument("--refresh", action="store_true", help="両エンジンの利用枠を更新してから表示する")
    temporary = help_text.add_command(
        sub,
        "temporary",
        **help_text.HELP["atk config temporary"],
    )
    actions = help_text.add_subcommands(temporary, dest="temporary_action", required=True)
    set_ = help_text.add_command(
        actions,
        "set",
        **help_text.HELP["atk config temporary set"],
    )
    set_.add_argument("key", help="一時指定する工程モデル・接続使用順・速度・待機方針の設定キー")
    set_.add_argument("value", help="通常設定と同じ書式の設定値")
    set_.add_argument("--until", help="タイムゾーンを含む期限。省略時は明示解除まで")
    help_text.add_command(
        actions,
        "clear",
        **help_text.HELP["atk config temporary clear"],
    )
    help_text.add_command(
        actions,
        "show",
        **help_text.HELP["atk config temporary show"],
    )


def dispatch(
    args: argparse.Namespace,
    *,
    resolve: Callable[[str], str],
    parse: Callable[[str], list[tuple[str, str, str]]],
    refresh: Callable[..., None] | None,
) -> None:
    if args.config_subcommand == "temporary":
        if args.temporary_action == "set":
            update_temporary(args.key, args.value, args.until)
        elif args.temporary_action == "clear":
            update_temporary(None, None, None)
        state = read_settings()
        print(
            json.dumps(
                {"temporary": {key: state.get(key) for key in ("values", "until")}, "effective": temporary_settings()},
                ensure_ascii=False,
            )
        )
        return
    if args.refresh:
        if refresh is None:
            raise ValueError("利用枠の更新機能が接続されていない")
        refresh(force=True)
    candidates = parse(resolve("execute_model"))
    try:
        selected, reason = select_candidates(
            candidates,
            "execute",
            automatic=resolve("usage_auto_allocation") == "true",
            activity_mode=resolve("usage_activity_mode"),
        )
    except ValueError as error:
        selected, reason = [], f"候補不足: {error}。config set execute_modelで候補を確認する"
    engines = ",".join(dict.fromkeys(item[0] for item in selected)) or "なし"
    print(f"優先順: {engines}。{reason}")
    print("統括: 保存された候補を維持し、通常の利用上限では回復を待つ")
    observed = usage_observation.snapshot()
    visible_windows = _profile_windows(observed)
    for engine in ("claude", "codex"):
        if not any(window["engine"] == engine for window in visible_windows):
            print(f"{engine}: 利用枠は取得不明。config usage --refreshで取得する")
    for window in visible_windows:
        remaining = "不明" if window["used_percent"] is None else f"{100 - window['used_percent']:g}%"
        resets = (
            "不明"
            if window["resets_at"] is None
            else datetime.datetime.fromtimestamp(window["resets_at"], datetime.UTC).astimezone().isoformat()
        )
        sampled = datetime.datetime.fromtimestamp(window["observed_at"], datetime.UTC).astimezone().isoformat()
        label = json.dumps([window["engine"], window["window_id"], window["scope"]], ensure_ascii=False)
        pace = observed.get("rates", {}).get(usage_observation.rate_key(usage_observation.QuotaWindow(**window)))
        pace_text = "不明" if pace is None else f"{pace:g}ポイント/稼働秒"
        fresh = time.time() - window["observed_at"] <= 600 and (
            window["resets_at"] is None or window["resets_at"] > time.time()
        )
        validity = "判定対象" if fresh else "過去の観測（現在は取得不明）"
        print(f"{label}: 観測残量={remaining}、回復={resets}、ペース={pace_text}、最終観測={sampled}、{validity}")
    normal = config_settings.load_config()
    model_settings = {
        key: {
            "stored": normal.get(key, default),
            "effective": resolve(key),
            "environment_override": bool(os.environ.get(config_settings.config_env_name(key))),
        }
        for key, default in config_settings.MODEL_DEFAULTS.items()
    }
    running = []
    for session_id in registry_index.pending_session_ids():
        info = session_registry.resolve(session_id).resume_info
        if info is not None and info.status == "running":
            running.append(
                {
                    "session_id": session_id,
                    "engine": info.engine,
                    "model": info.model,
                    "effort": info.effort,
                    "model_type": info.model_type,
                    "fast_mode": info.fast_mode,
                }
            )
    claude_rights = json.loads(resolve("claude_reset_inventory"))
    permissions = json.loads(resolve("usage_reset_permissions"))
    now = time.time()
    reset_advice = []
    for right in claude_rights:
        allowed = any(
            permission["engine"] == "claude"
            and permission["account"] == right["account"]
            and permission["scope"] == right["scope"]
            and permission["count"] > 0
            and deadline(permission["from"]) <= now < deadline(permission["until"])
            for permission in permissions
        )
        tight = any(
            window.engine == "claude"
            and window.account == right["account"]
            and window.used_percent is not None
            and window.used_percent >= 90
            and window.resets_at is not None
            and window.resets_at - now > 300
            and (right["scope"] == "all" or right["scope"] in {window.window_id, window.scope})
            for window in [usage_observation.QuotaWindow(**item) for item in visible_windows]
        )
        reset_advice.append(
            {
                "id": right["id"],
                "until": right["until"],
                "scope": right["scope"],
                "recommended": bool(running) and allowed and tight and right["count"] > 0 and now < deadline(right["until"]),
                "action": (
                    "https://claude.ai/settings/usage で対象と期限を確認して手動消費後、"
                    "在庫を更新しconfig usage --refreshで再取得"
                ),
            }
        )
    print(
        json.dumps(
            {
                "usage": {**observed, "windows": visible_windows},
                "settings": model_settings,
                "temporary": {key: read_settings().get(key) for key in ("values", "until")},
                "metadata": {engine: read_metadata(engine) for engine in ("claude", "codex")},
                "refresh_failures": read_settings().get("refresh_failures", []),
                "running_sessions": running,
                "application_boundary": "稼働中のturnは終了させず、次の新規起動・継続境界で実効候補を適用",
                "claude_reset_advice": reset_advice,
            },
            ensure_ascii=False,
        )
    )
