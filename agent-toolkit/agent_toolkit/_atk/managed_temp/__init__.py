"""managed-temp（管理対象一時ディレクトリ）の作成、セッションのmanaged-tempの解決、回収と期限掃引を責務別のサブモジュールに持つパッケージ。

領域は一時root直下の実体だけで管理し、登録簿・管理情報・ロックを新たに作成しない。
パッケージ外の呼び出し元が使う名前だけを定義元から再exportする。
"""

from agent_toolkit._atk.managed_temp.creation import (
    SESSION_TEMP_PREFIX,
    create_managed_temp,
    create_output_temp,
    create_session_temp,
    find_session_temp,
    resolve_session_temp,
    session_temp_path,
)
from agent_toolkit._atk.managed_temp.errors import (
    ManagedTempError,
)
from agent_toolkit._atk.managed_temp.paths import (
    MAX_AGE_DAYS,
    contained_target,
    temp_root_path,
)
from agent_toolkit._atk.managed_temp.removal import (
    PLUGIN_ROOT_PREFIX,
    cleanup_managed_temp,
    list_managed_temp,
    plugin_root_in_use,
    top_level_area,
    validate_managed_temp,
)
from agent_toolkit._atk.managed_temp.sweep import (
    SweepResult,
    sweep_managed_temp,
)

__all__ = [
    "MAX_AGE_DAYS",
    "PLUGIN_ROOT_PREFIX",
    "SESSION_TEMP_PREFIX",
    "ManagedTempError",
    "SweepResult",
    "cleanup_managed_temp",
    "contained_target",
    "create_managed_temp",
    "create_output_temp",
    "create_session_temp",
    "find_session_temp",
    "list_managed_temp",
    "plugin_root_in_use",
    "resolve_session_temp",
    "session_temp_path",
    "sweep_managed_temp",
    "temp_root_path",
    "top_level_area",
    "validate_managed_temp",
]
