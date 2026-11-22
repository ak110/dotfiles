from __future__ import annotations

from agent_toolkit._atk import config_migration, config_settings
from agent_toolkit._common import runtime_environment
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.usage_settings import temporary_settings


def connection_order(engine: str, role: str) -> tuple[str, ...]:
    return config_settings.parse_connection_order(resolve_mutable_setting(config_settings.connection_order_key(engine, role)))


def connections_configured(engine: str) -> bool:
    """エンジンの認証キー2つと使用順2つのいずれかを、環境変数か保存値で設定しているかを返す。

    どれも設定していない環境は、接続を管理せず親環境と通常の認証解決を引き継ぐ。
    使用順には初期値があるため、実効値ではなく明示の有無で判定する。
    """
    keys = [key for key in config_settings.CONNECTION_DEFAULTS if key.startswith(f"{engine}_")]
    if not keys:
        raise KeyError(engine)
    config_migration.check_legacy_environment()
    config_migration.migrate_legacy_settings()
    stored = config_settings.load_config()
    return any(runtime_environment.environment().get(config_settings.config_env_name(key), "") or key in stored for key in keys)


def resolve_mutable_setting(key: str, *, fallback: str | None = None) -> str:
    if key not in config_settings.MUTABLE_KEY_DEFAULTS:
        raise KeyError(key)
    config_migration.check_legacy_environment()
    config_migration.migrate_legacy_settings()
    env_name = config_settings.config_env_name(key)
    env_value = runtime_environment.environment().get(env_name, "")
    value = (
        config_settings.raw_mutable_setting(key)
        if fallback is None
        else config_settings.raw_mutable_setting(key, fallback=fallback)
    )
    if not env_value:
        value = temporary_settings().get(key, value)
    try:
        config_settings.validate_mutable_setting(key, value)
    except ValueError as error:
        source = f"環境変数{env_name}" if env_value else f"設定キー{key}"
        next_action = (
            f"環境変数{env_name}を受理可能書式の値へ直すか解除して再実行する"
            if env_value
            else f"`atk config set {key} <VALUE>`で受理可能書式の値へ直して再実行する"
        )
        raise ActionableError(f"{source}の値が不正です（値: {value}）。{error}", next_action=next_action) from error
    return config_settings.normalize_setting(key, value)
