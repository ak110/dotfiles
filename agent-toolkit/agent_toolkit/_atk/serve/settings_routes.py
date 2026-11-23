import argparse
import asyncio
import json
import pathlib
import typing

import quart

from agent_toolkit._atk import config, config_repositories_cli, config_settings, config_usage
from agent_toolkit._atk.serve.config import ServeConfig
from agent_toolkit._atk.serve.shell_routes import json_no_store, no_store, render_index, safe_base_path
from agent_toolkit._atk.wi.web_input import WebInputError, web_input_error_from
from agent_toolkit._common import runtime_environment, usage_observation, usage_settings


def settings_snapshot(serve: ServeConfig) -> dict[str, typing.Any]:
    effective = config.resolved_settings(pathlib.Path.home())
    saved = config_settings.load_config()
    temporary = usage_settings.temporary_settings()
    settings = {}
    for key, default in config_settings.MUTABLE_KEY_DEFAULTS.items():
        env = config_settings.config_env_name(key)
        source = (
            env
            if runtime_environment.environment().get(env)
            else "一時指定"
            if key in temporary
            else "保存値"
            if key in saved
            else "省略時の値"
        )
        settings[key] = {
            "effective": effective.pop(key),
            "saved": saved.get(key, default),
            "default": default,
            "source": source,
            "description": config_settings.setting_description(key),
            "format": config_settings.setting_format(key),
            "choices": config_settings.SETTING_CHOICES.get(key),
            "array": key in config_settings.ARRAY_KEYS,
            "temporary": config_usage.allowed_temporary_key(key),
        }
    usage = usage_observation.snapshot()
    state = usage_settings.read_settings()
    return {
        "settings": settings,
        "paths": effective,
        "listen": {"serve_host": [serve.host, serve.host_source], "serve_port": [serve.port, serve.port_source]},
        "presets": config_settings.PRESET_MODELS,
        "temporary": {"values": temporary, "until": state.get("until")},
        "repositories": json.loads(config_repositories_cli.update_repositories(argparse.Namespace(repos_subcommand="list"))),
        "model_candidates": {engine: sorted(names) for engine, names in config_settings.KNOWN_MODELS.items()},
        "usage": config_usage.profile_windows(usage),
        "model_order_policy": config_usage.MODEL_ORDER_POLICY,
    }


def _text(payload: dict[str, typing.Any], name: str, *, optional: bool = False) -> str | None:
    value = payload.get(name)
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name}は文字列で指定してください")
    return value


def update_settings(payload: dict[str, typing.Any]) -> None:
    action = payload.get("action")
    if action == "temporary-clear":
        config_usage.update_temporary(None, None, None)
        return
    if action == "preset":
        preset = _text(payload, "preset")
        if preset not in config_settings.PRESET_MODELS:
            raise ValueError("登録済みのプリセットを選んでください")
        config.save_mutable_settings(config_settings.PRESET_MODELS[preset])
        return
    if action == "repo":
        operation = _text(payload, "operation")
        repo = _text(payload, "repo")
        if operation not in ("add", "set", "remove") or not repo:
            raise ValueError("登録操作とリポジトリを指定してください")
        fields = ("edit", "path", "platforms", "branches", "default_branch", "merge_from")
        values = {name: _text(payload, name, optional=True) for name in fields}
        process_loop = payload.get("process_loop")
        if process_loop is not None and not isinstance(process_loop, bool):
            raise ValueError("process-loopの対象かどうかを指定してください")
        if operation == "add" and not values["edit"]:
            raise ValueError("編集可否を指定してください")
        config_repositories_cli.update_repositories(
            argparse.Namespace(repos_subcommand=operation, repo=repo, process_loop=process_loop, **values)
        )
        return
    key, value = _text(payload, "key"), _text(payload, "value")
    if key is None or value is None or key not in config_settings.MUTABLE_KEY_DEFAULTS:
        raise ValueError("変更可能な設定を選んでください")
    if action == "set":
        config.save_mutable_settings({key: value})
    elif action == "temporary":
        config_usage.update_temporary(key, value, _text(payload, "until", optional=True))
    elif action in ("array-add", "array-remove") and key in config_settings.ARRAY_KEYS:
        updated = config.array_setting_update(key, value, remove=action == "array-remove")
        if updated is not None:
            config.save_mutable_settings({key: updated})
    else:
        raise ValueError("設定の操作が不正です")


def register_settings_routes(app: quart.Quart, serve: ServeConfig) -> None:
    lock = asyncio.Lock()

    @app.get("/settings")
    async def settings_index() -> quart.Response:
        return no_store(
            render_index(app.config["PLANS_CONTEXT"], safe_base_path(quart.request.root_path), "settings"),
            "text/html; charset=utf-8",
        )

    @app.get("/api/settings")
    async def settings_get() -> quart.Response:
        async with lock:
            return json_no_store(await asyncio.to_thread(settings_snapshot, serve))

    @app.post("/api/settings")
    async def settings_post() -> quart.Response:
        payload = await quart.request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise WebInputError("JSONオブジェクトを指定してください", next_action="画面から値を入力して保存する")
        async with lock:
            try:
                await asyncio.to_thread(update_settings, payload)
            except (ValueError, TypeError) as error:
                raise web_input_error_from(error, next_action="説明で受理される値を確認し、入力を直して保存する") from error
        return json_no_store({"saved": True})
