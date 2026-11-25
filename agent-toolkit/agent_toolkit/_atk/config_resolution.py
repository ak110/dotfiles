from __future__ import annotations

from collections.abc import Mapping

from agent_toolkit._atk import config_migration, config_settings
from agent_toolkit._common import runtime_environment
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.usage_settings import temporary_settings


def connection_order(engine: str, role: str) -> tuple[str, ...]:
    return config_settings.parse_connection_order(resolve_mutable_setting(config_settings.connection_order_key(engine, role)))


def connections_configured(engine: str) -> bool:
    """使用順・Codexのproviderの明示、またはClaudeの認証ファイルの有無を返す。  pyfltr: allow-comment

    どれも設定していない環境は、接続を管理せず親環境と通常の認証解決を引き継ぐ。
    使用順には初期値があるため、実効値ではなく明示の有無で判定する。
    """
    keys = [key for key in config_settings.CONNECTION_DEFAULTS if key.startswith(f"{engine}_")]
    if not keys:
        raise KeyError(engine)
    config_migration.check_legacy_environment()
    config_migration.migrate_legacy_settings()
    if engine == "claude" and any(
        (config_settings.config_dir() / name).exists() for name in config_settings.CLAUDE_ENV_FILENAMES.values()
    ):
        return True
    stored = config_settings.load_config()
    if (path := config_settings.shared_settings_path()) is not None:
        stored = config_settings.load_shared_settings(path) | stored
    return any(runtime_environment.environment().get(config_settings.config_env_name(key), "") or key in stored for key in keys)


def mutable_setting_source(
    key: str,
    *,
    environment_source: str,
    temporary: Mapping[str, object],
    local: Mapping[str, object],
    shared: Mapping[str, object],
    shared_target: bool,
) -> str:
    if environment_source:
        return environment_source
    if key in temporary:
        return "一時指定"
    if key in local:
        return "移行前のローカル保存値" if shared_target else "保存値"
    if key in shared:
        return "共有"
    return "省略時の値"


def resolve_mutable_setting(key: str, *, fallback: str | None = None, read_only: bool = False) -> str:
    if key not in config_settings.MUTABLE_KEY_DEFAULTS:
        raise KeyError(key)
    config_migration.check_legacy_environment()
    if not read_only:
        config_migration.migrate_legacy_settings()
    env_name = config_settings.config_env_name(key)
    env_value, source_env = (
        config_migration.remote_hosts_environment()
        if key == "remote_hosts"
        else (runtime_environment.environment().get(env_name, ""), env_name)
    )
    value = env_value or temporary_settings().get(key)
    if value is None:
        value = (
            config_settings.raw_mutable_setting(key)
            if fallback is None
            else config_settings.raw_mutable_setting(key, fallback=fallback)
        )
    try:
        config_settings.validate_mutable_setting(key, value)
    except ValueError as error:
        source = f"環境変数{source_env}" if env_value else f"設定キー{key}"
        next_action = (
            f"環境変数{source_env}を受理可能書式の値へ直すか解除して再実行する"
            if env_value
            else f"`atk config set {key} <VALUE>`で受理可能書式の値へ直して再実行する"
        )
        raise ActionableError(f"{source}の値が不正です（値: {value}）。{error}", next_action=next_action) from error
    return config_settings.normalize_setting(key, value)
