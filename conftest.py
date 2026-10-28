"""pytest conftest: `pytools/`・`scripts/`のテストへ開発機の状態からの隔離を自動で適用する。

隔離の定義は`agent_toolkit._testing.isolation`が持ち、`agent-toolkit/conftest.py`も同じ定義を適用する。
依存ライブラリのDEBUGログを取り込みから外す定義（`agent_toolkit._testing.dependency_logging`）も同じ形で適用する。
"""

from agent_toolkit._testing import dependency_logging, isolation

# Codex投影の同一テストは原本で実行し、投影自体は専用の不変条件テストで検証する。
collect_ignore = ["agent-toolkit-codex"]

isolated_path_value = isolation.isolated_path_value
isolate_development_state = isolation.isolate_development_state
host_environ = isolation.host_environ
share_package_caches = isolation.share_package_caches
quiet_dependency_loggers = dependency_logging.quiet_dependency_loggers
