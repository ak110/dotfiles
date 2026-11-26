"""利用枠の確認と、通常設定を変更しない期限付きの一時指定。"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk import config_settings, help_text
from agent_toolkit._atk.environment import is_human_terminal
from agent_toolkit._common import claude_usage_limit, registry_index, session_registry, usage_observation, usage_settings
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.usage_settings import deadline, read_metadata, read_settings, settings_path, temporary_settings

MODEL_ORDER_POLICY = "候補は工程別モデル設定の順に試す。利用枠の観測は残量表示とreset判断に使い、候補の順序は変更しない。"


def allowed_temporary_key(key: str) -> bool:
    return key in {
        *config_settings.MODEL_DEFAULTS,
        *config_settings.CONNECTION_ORDER_KEYS,
        "codex_fast_mode",
        "claude_usage_limit_wait",
        "codex_usage_limit_wait",
    }


def update_temporary(key: str | None, value: str | None, until: str | None) -> tuple[str | None, tuple[str, ...]] | None:
    if key is not None:
        if not allowed_temporary_key(key):
            raise ActionableError(
                f"{key}は一時指定の対象外",
                next_action="一時指定に使えるキーを `atk config temporary set --help` で確認する",
            )
        if value is not None:
            try:
                config_settings.validate_mutable_setting(key, value)
            except ValueError as error:
                raise ActionableError(
                    f"{key}の一時指定値が不正: {error}",
                    next_action=f"値を直して `atk config temporary set {key} <VALUE>` を再実行する",
                ) from error
    if until is not None:
        try:
            deadline(until)
        except ValueError as error:
            raise ActionableError(
                "一時指定の期限は時差付きISO 8601の日時で指定する",
                next_action="例: --until 2030-01-01T12:00:00+09:00 を付けて `atk config temporary set` を再実行する",
            ) from error
    changed_deadline = None
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+", encoding="utf-8") as lock:
        acquire_lock(lock)
        try:
            state = read_settings()
            if key is None:
                state.pop("values", None)
                state.pop("until", None)
            elif value is None:
                values = temporary_settings()
                values.pop(key, None)
                if values:
                    state["values"] = values
                else:
                    state.pop("values", None)
                    state.pop("until", None)
            else:
                values = temporary_settings()
                previous_until = state.get("until")
                others = tuple(sorted(values.keys() - {key}))
                old_time = deadline(previous_until) if isinstance(previous_until, str) and others else None
                new_time = deadline(until) if until is not None else None
                if others and old_time != new_time:
                    changed_deadline = (previous_until, others)
                values[key] = config_settings.normalize_setting(key, value)
                state.update(values=values, until=until)
            atomic_write(path, json.dumps(state, ensure_ascii=False) + "\n")
        finally:
            release_lock(lock)
    return changed_deadline


def profile_windows(observed: dict[str, Any]) -> list[dict[str, Any]]:
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


def cli_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    display_fields = {"group", "severity", "is_active", "limit_name"}
    return [{key: value for key, value in window.items() if key not in display_fields} for window in windows]


def _cli_metadata(metadata: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    display_fields = {
        "claude": {"extra_usage_utilization"},
        "codex": {
            "weekly_window",
            "ordinaryUsageAllowed",
            "spendControlReached",
            "rateLimitReachedType",
            "credits",
            "reset_credit_details",
        },
    }
    return {
        engine: {key: value for key, value in profile.items() if key not in display_fields[engine]}
        for engine, profile in metadata.items()
    }


def _normalized_window(original: dict[str, Any]) -> dict[str, Any]:
    window = original.copy()
    kind, scope = window["window_id"], window["scope"]
    if window["engine"] != "claude":
        return window
    if kind in claude_usage_limit.REPORT_WINDOWS:
        kind, scope = claude_usage_limit.REPORT_WINDOWS[kind]
    elif kind in {"session", "weekly_all"}:
        scope = "all"
    elif kind == "weekly_scoped" and scope.startswith("{"):
        try:
            parts = json.loads(scope)
        except ValueError:
            parts = {}
        model = parts.get("model") if isinstance(parts, dict) else None
        if isinstance(model, dict):
            model = model.get("display_name")
        if isinstance(model, str):
            family = next((part for part in ("opus", "sonnet") if part in model.lower()), "")
            if family:
                scope = f"model:{family}"
    window.update(
        window_id=kind,
        scope=scope,
        aliases=sorted({original["window_id"], original["scope"], kind, scope}),
        duration_seconds={"session": 18000, "weekly_all": 604800, "weekly_scoped": 604800}.get(kind),
    )
    return window


def display_windows(windows: list[dict[str, Any]], history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for original in windows:
        window = _normalized_window(original)
        grouped.setdefault((window["engine"], window["window_id"], window["scope"]), []).append(window)

    visible = []
    for observations in grouped.values():
        ordered = sorted(observations, key=lambda window: window["observed_at"], reverse=True)
        latest = ordered[0]
        latest["usage_observed_at"] = latest["observed_at"]
        if latest["engine"] == "claude" and latest["used_percent"] is None and latest["resets_at"] is not None:
            candidates = [
                _normalized_window(prior)
                for prior in history or []
                if prior["engine"] == "claude"
                and prior["account"] == latest["account"]
                and prior["resets_at"] == latest["resets_at"]
                and prior["used_percent"] is not None
                and prior["observed_at"] < latest["observed_at"]
            ]
            older = [
                prior
                for prior in (*ordered[1:], *candidates)
                if prior["window_id"] == latest["window_id"]
                and prior["scope"] == latest["scope"]
                and prior["resets_at"] == latest["resets_at"]
                and prior["used_percent"] is not None
            ]
            if older:
                prior = max(older, key=lambda row: row["observed_at"])
                latest.update(used_percent=prior["used_percent"], usage_observed_at=prior["observed_at"])
        visible.append(latest)
    return sorted(visible, key=lambda item: (item["engine"], item["duration_seconds"] or float("inf"), item["window_id"]))


def reset_editor_snapshot(
    windows: list[dict[str, Any]], history: list[dict[str, Any]], state: dict[str, Any]
) -> dict[str, Any]:
    claude = read_metadata("claude")
    codex = read_metadata("codex")
    return {
        "accounts": {
            "claude": {"account": claude.get("account"), "plan": claude.get("contract")},
            "codex": {"account": codex.get("permission_account"), "plan": codex.get("contract")},
        },
        "claude_inventory": state.get("claude_reset_inventory", []),
        "codex_weekly": codex.get("weekly_window"),
        "windows": display_windows(windows, history),
    }


def build_parser(sub: Any) -> None:
    usage = help_text.add_command(
        sub,
        "usage",
        **help_text.HELP["atk config usage"],
    )
    usage.add_argument(
        "--set-claude-reset-inventory", metavar="JSON", help="Claudeのreset登録在庫を置き換える（許可は変更しない）"
    )
    usage.add_argument("--json", action="store_true", help="詳細をJSONで表示する")
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
    clear = help_text.add_command(
        actions,
        "clear",
        **help_text.HELP["atk config temporary clear"],
    )
    clear.add_argument("key", nargs="?", help="このキーの一時指定だけを解除する。省略時は全て解除する。")
    show = help_text.add_command(
        actions,
        "show",
        **help_text.HELP["atk config temporary show"],
    )
    for command in (set_, clear, show):
        command.add_argument("--json", action="store_true", help="一時指定の状態をJSONで表示する")


def _display_scope(scope: str) -> str:
    if not scope or scope == "all":
        return "全体"
    if not scope.startswith("{"):
        return scope
    try:
        details = json.loads(scope)
    except ValueError:
        return "対象の詳細は--jsonで確認"
    if not isinstance(details, dict):
        return "対象の詳細は--jsonで確認"
    model = details.get("model")
    model_name = model.get("display_name") if isinstance(model, dict) else model
    return " / ".join(value for value in (model_name, details.get("surface")) if isinstance(value, str) and value) or (
        "対象の詳細は--jsonで確認"
    )


def dispatch(
    args: argparse.Namespace,
    *,
    resolve: Callable[[str], str],
    refresh: Callable[..., None] | None,
) -> None:
    if args.config_subcommand == "temporary":
        change = None
        if args.temporary_action == "set":
            change = update_temporary(args.key, args.value, args.until)
        elif args.temporary_action == "clear":
            update_temporary(args.key, None, None)
        state = read_settings()
        effective = temporary_settings()
        temporary = {key: state.get(key) for key in ("values", "until")}
        if args.json or not is_human_terminal(sys.stdout):
            print(json.dumps({"temporary": temporary, "effective": effective}, ensure_ascii=False))
        elif temporary["values"]:
            if change is not None:
                old_until, affected = change
                print(f"既存の一時指定の期限を変更: {old_until or '明示解除まで'} → {args.until or '明示解除まで'}")
                for key in affected:
                    print(f"  対象: {key}")
            print(f"一時指定の期限: {temporary['until'] or '明示解除まで'}")
            for key, value in sorted(temporary["values"].items()):
                status = "適用期間内" if key in effective else "期限切れ"
                print(f"{key}: {value}（{status}）")
        else:
            print("一時指定はない。通常設定を使う。")
        return
    if args.set_claude_reset_inventory is not None:
        usage_settings.save_claude_reset_inventory(args.set_claude_reset_inventory)
    if args.refresh:
        if refresh is None:
            raise ValueError("利用枠の更新機能が接続されていない")
        refresh(force=True)
    observed = usage_observation.snapshot()
    visible_windows = profile_windows(observed)
    settings = read_settings()
    metadata = {engine: read_metadata(engine) for engine in ("claude", "codex")}
    claude_rights = settings.get("claude_reset_inventory", [])
    if not args.json:
        print(MODEL_ORDER_POLICY)
        for engine in ("claude", "codex"):
            if not any(window["engine"] == engine for window in visible_windows):
                print(f"{engine}: 利用枠は取得不明。config usage --refreshで取得する")
        for window in visible_windows:
            quota = usage_observation.QuotaWindow(**window)
            remaining = "不明" if window["used_percent"] is None else f"{100 - window['used_percent']:g}%"
            resets = (
                "不明"
                if window["resets_at"] is None
                else datetime.datetime.fromtimestamp(window["resets_at"], datetime.UTC)
                .astimezone()
                .isoformat(timespec="seconds")
            )
            sampled = (
                datetime.datetime.fromtimestamp(window["observed_at"], datetime.UTC).astimezone().isoformat(timespec="seconds")
            )
            label = f"{quota.engine} / {quota.window_id} / {_display_scope(quota.scope)}"
            pace = observed.get("rates", {}).get(usage_observation.rate_key(quota))
            pace_text = "不明" if pace is None else f"{pace:g}ポイント/稼働秒"
            print(f"{label}: 観測残量={remaining}、回復={resets}、ペース={pace_text}、最終観測={sampled}")
        count = metadata["codex"].get("reset_available_count")
        count_text = f"{count:g}" if count is not None else "不明"
        print(f"Codex reset在庫: {count_text}")
        print(f"Claude reset登録在庫: {sum(right['count'] for right in claude_rights)}（対象・期限はconfig usage --json）")
        failures = settings.get("refresh_failures", [])
        for failure in failures:
            print(f"取得失敗: {failure}")
        if failures:
            print("取得失敗の理由を確認し、atk config usage --refreshで再取得する")
        return
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
                "usage": {
                    **observed,
                    "windows": cli_windows(visible_windows),
                    "current_windows": cli_windows(observed["current_windows"]),
                },
                "model_order_policy": MODEL_ORDER_POLICY,
                "settings": model_settings,
                "temporary": {key: settings.get(key) for key in ("values", "until")},
                "metadata": _cli_metadata(metadata),
                "refresh_failures": settings.get("refresh_failures", []),
                "running_sessions": running,
                "application_boundary": "稼働中のturnは終了させず、次の新規起動・継続境界で実効候補を適用",
                "claude_reset_inventory": claude_rights,
                "claude_reset_advice": reset_advice,
            },
            ensure_ascii=False,
        )
    )
