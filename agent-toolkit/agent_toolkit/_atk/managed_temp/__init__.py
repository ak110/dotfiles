"""managed-temp（管理対象一時ディレクトリ）の作成、検証、登録簿、列挙と回収、CLIを責務別のサブモジュールに持つパッケージ。

パッケージ外の呼び出し元が使う名前だけを定義元から再exportする。
"""

from agent_toolkit._atk.managed_temp.cli import (
    build_parser,
    dispatch,
    main,
)
from agent_toolkit._atk.managed_temp.creation import (
    SESSION_TEMP_PREFIX,
    create_managed_temp,
    create_session_temp,
)
from agent_toolkit._atk.managed_temp.errors import (
    ManagedTempError,
)
from agent_toolkit._atk.managed_temp.inventory import (
    SweepResult,
    cleanup_managed_temp,
    list_managed_temp,
    sweep_expired_managed_temp,
    sweep_managed_temp,
)
from agent_toolkit._atk.managed_temp.validation import (
    validate_managed_temp,
)

__all__ = [
    "ManagedTempError",
    "SESSION_TEMP_PREFIX",
    "SweepResult",
    "build_parser",
    "cleanup_managed_temp",
    "create_managed_temp",
    "create_session_temp",
    "dispatch",
    "list_managed_temp",
    "main",
    "sweep_expired_managed_temp",
    "sweep_managed_temp",
    "validate_managed_temp",
]
