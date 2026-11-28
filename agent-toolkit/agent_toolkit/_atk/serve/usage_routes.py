import asyncio
import subprocess
import time
from typing import Any

import quart

from agent_toolkit._atk import config, config_usage, usage_refresh
from agent_toolkit._atk.serve.shell_routes import json_no_store, no_store, render_index, safe_base_path
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import process_tree, usage_observation, usage_settings

_WINDOW_FIELDS = (
    "engine",
    "window_id",
    "used_percent",
    "resets_at",
    "observed_at",
    "usage_observed_at",
    "scope",
    "duration_seconds",
    "rejected",
    "group",
    "severity",
    "is_active",
    "limit_name",
)
_METADATA_FIELDS = {
    "claude": (
        "contract",
        "extra_usage_enabled",
        "extra_usage_spend",
        "extra_usage_limit",
        "extra_usage_utilization",
        "currency",
    ),
    "codex": (
        "contract",
        "ordinaryUsageAllowed",
        "spendControlReached",
        "rateLimitReachedType",
        "credits",
        "reset_available_count",
        "reset_credit_details",
    ),
}


def _reset_setting(engine: str) -> dict[str, Any]:
    try:
        enabled = config.resolve_mutable_setting(f"{engine}_auto_reset") == "true"
    except (OSError, ValueError) as error:
        return {"enabled": None, "error": f"設定を読み取れません: {error}"}
    return {"enabled": enabled}


def _display_data(refresh: dict[str, Any] | None = None) -> dict[str, Any]:
    state = usage_settings.read_settings() if refresh is None else refresh
    profiles = {engine: usage_settings.read_metadata(engine) for engine in _METADATA_FIELDS}
    observed = usage_observation.snapshot(include_history=True)
    windows = config_usage.display_windows(config_usage.profile_windows(observed), observed["history"])
    reset_settings = {engine: _reset_setting(engine) for engine in _METADATA_FIELDS}
    now = time.time()
    result: dict[str, Any] = {
        "windows": [{key: window[key] for key in _WINDOW_FIELDS if key in window} for window in windows],
        "metadata": {
            engine: {key: profiles[engine][key] for key in fields if key in profiles[engine]}
            for engine, fields in _METADATA_FIELDS.items()
        },
        "reset_settings": reset_settings,
        "claude_reset_recommended": any(
            advice["recommended"]
            for advice in config_usage.claude_manual_reset_advice(
                state.get("claude_reset_inventory", []),
                windows,
                enabled=reset_settings["claude"]["enabled"] is True,
                now=now,
            )
        ),
        "claude_inventory": [
            {key: entry[key] for key in ("scope", "count", "until")} for entry in state.get("claude_reset_inventory", [])
        ],
    }
    if state.get("refresh_context") == usage_settings.context_id():
        observed_at = state.get("last_refresh_at")
        failures = state.get("refresh_failures", [])
        reuse = (
            isinstance(observed_at, (int, float))
            and not failures
            and now - observed_at < usage_refresh.DISPLAY_REFRESH_INTERVAL_SEC
        )
        result.update(last_refresh_at=observed_at, refresh_failures=failures, refresh_due=not reuse)
    return result


def register_usage_routes(app: quart.Quart) -> None:
    agy_task: asyncio.Task[None] | None = None
    agy_reclaim: process_tree.ReclaimError | None = None
    agy_windows: list[dict[str, Any]] = []
    agy_checked_at = float("-inf")
    agy_observed_at: float | None = None
    agy_stale = False

    async def refresh_antigravity() -> None:
        nonlocal agy_windows, agy_checked_at, agy_observed_at, agy_stale, agy_reclaim
        try:
            if agy_reclaim is not None:
                if await asyncio.to_thread(agy_reclaim.retry):
                    return
                agy_reclaim = None
            agy_windows = await asyncio.to_thread(usage_refresh.read_antigravity_usage)
        except process_tree.ReclaimError as error:
            agy_reclaim = error
            agy_stale = True
        except OSError, ValueError, subprocess.TimeoutExpired:
            agy_stale = True
        else:
            agy_observed_at = time.time() if agy_windows else None
            agy_stale = False
        finally:
            agy_checked_at = time.monotonic()

    @app.get("/usage")
    async def usage_index() -> quart.Response:
        return no_store(
            render_index(app.config["PLANS_CONTEXT"], safe_base_path(quart.request.root_path), "usage"),
            "text/html; charset=utf-8",
        )

    @app.get("/api/usage")
    async def usage_view() -> quart.Response:
        return json_no_store(await asyncio.to_thread(_display_data))

    @app.post("/api/usage/antigravity")
    async def usage_antigravity() -> quart.Response:
        nonlocal agy_task
        payload = await quart.request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("force"), bool):
            raise WebInputError("取得方法を指定してください", next_action="利用枠画面を開き直して再取得する")
        due = time.monotonic() - agy_checked_at >= usage_refresh.DISPLAY_REFRESH_INTERVAL_SEC
        if (agy_task is None or agy_task.done()) and (due or payload["force"]):
            agy_task = asyncio.create_task(refresh_antigravity())
        if agy_task is not None and not agy_task.done():
            # HTTP要求が取り消されても、取得または同じ子の回収が終わるまで所有を保つ。 pyfltr: allow-comment
            await asyncio.shield(agy_task)
        return json_no_store({"windows": agy_windows, "observed_at": agy_observed_at, "stale": agy_stale})

    @app.post("/api/usage/reset-setting")
    async def usage_reset_setting() -> quart.Response:
        payload = await quart.request.get_json(silent=True)
        if (
            not isinstance(payload, dict)
            or payload.get("engine") not in ("claude", "codex")
            or not isinstance(payload.get("enabled"), bool)
        ):
            raise WebInputError("リセット設定が不正です", next_action="利用枠画面で有効か無効を選ぶ")
        engine = payload["engine"]
        try:
            await asyncio.to_thread(
                config.update_settings,
                {
                    "action": "set",
                    "key": f"{engine}_auto_reset",
                    "value": str(payload["enabled"]).lower(),
                },
            )
        except (OSError, ValueError) as error:
            raise WebInputError(str(error), next_action="設定と共有先を確認してから利用枠画面で再取得する") from error
        try:
            return json_no_store(await asyncio.to_thread(_display_data))
        except OSError, ValueError:
            return json_no_store(
                {
                    "reset_settings": {engine: {"enabled": payload["enabled"]}},
                    "claude_reset_recommended": False,
                    "observation_error": "利用枠を読み取れません。利用枠の観測を再取得してください。",
                }
            )

    @app.post("/api/usage/refresh")
    async def usage_refresh_request() -> quart.Response:
        payload = await quart.request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("force"), bool):
            raise WebInputError("取得方法を指定してください", next_action="利用枠画面を開き直して再取得する")
        try:
            state = await asyncio.to_thread(usage_refresh.refresh_for_display, force=payload["force"])
            return json_no_store(await asyncio.to_thread(_display_data, state))
        except (OSError, ValueError) as error:
            raise WebInputError(
                "利用枠の取得結果を保存または読み取りできません", next_action="保存先を確認して再取得する"
            ) from error
