"""pytest conftest: `agent_toolkit/`・`skills/`のテストへ開発機の状態からの隔離を自動で適用する。

pytestは祖先ディレクトリのconftestだけを読むため、両ディレクトリの共通の祖先である本ファイルで適用する。
`agent-toolkit/`を起点に起動した場合はリポジトリ直下の`conftest.py`が読まれないため、
隔離の定義は`agent_toolkit._testing.isolation`が持ち、直下の`conftest.py`も同じ定義を適用する。
依存ライブラリのDEBUGログを取り込みから外す定義（`agent_toolkit._testing.dependency_logging`）も同じ形で適用する。
"""

from agent_toolkit._testing import dependency_logging, isolation

isolated_path_value = isolation.isolated_path_value
isolate_development_state = isolation.isolate_development_state
host_environ = isolation.host_environ
share_package_caches = isolation.share_package_caches
quiet_dependency_loggers = dependency_logging.quiet_dependency_loggers
