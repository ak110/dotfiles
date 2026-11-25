import asyncio
import json
import time
from typing import Any

import quart

from agent_toolkit._atk import config, config_usage, usage_refresh
from agent_toolkit._atk.serve.shell_routes import json_no_store, no_store, render_index, safe_base_path
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import usage_observation, usage_settings

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


def _display_data(refresh: dict[str, Any] | None = None) -> dict[str, Any]:
    state = usage_settings.read_settings() if refresh is None else refresh
    profiles = {engine: usage_settings.read_metadata(engine) for engine in _METADATA_FIELDS}
    observed = usage_observation.snapshot(include_history=True)
    windows = config_usage.display_windows(config_usage.profile_windows(observed), observed["history"])
    permissions = json.loads(config.resolve_mutable_setting("usage_reset_permissions"))
    now = time.time()
    result: dict[str, Any] = {
        "windows": [{key: window[key] for key in _WINDOW_FIELDS if key in window} for window in windows],
        "metadata": {
            engine: {key: profiles[engine][key] for key in fields if key in profiles[engine]}
            for engine, fields in _METADATA_FIELDS.items()
        },
        "permissions": [
            {key: entry[key] for key in ("engine", "scope", "count", "from", "until")}
            for entry in permissions
            if usage_settings.deadline(entry["until"]) > now
        ],
        "claude_inventory": [
            {key: entry[key] for key in ("scope", "count", "until")} for entry in state.get("claude_reset_inventory", [])
        ],
    }
    if state.get("refresh_context") == usage_settings.context_id():
        result.update(last_refresh_at=state.get("last_refresh_at"), refresh_failures=state.get("refresh_failures", []))
    return result


def register_usage_routes(app: quart.Quart) -> None:
    @app.get("/usage")
    async def usage_index() -> quart.Response:
        return no_store(
            render_index(app.config["PLANS_CONTEXT"], safe_base_path(quart.request.root_path), "usage"),
            "text/html; charset=utf-8",
        )

    @app.get("/api/usage")
    async def usage_view() -> quart.Response:
        return json_no_store(await asyncio.to_thread(_display_data))

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
