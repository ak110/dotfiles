"""`pytools/`配下のテストへ開発機の状態からの隔離が自動で適用されることを確かめる。"""

import pathlib
import sys
from collections.abc import Callable

import pytest
from agent_toolkit._testing import isolation, loop_isolation_support

from pytools import post_apply
from pytools._internal import claude_common, sync_report


def test_development_state_isolated_by_default(tmp_path: pathlib.Path) -> None:
    """この起動範囲のテストは、何も設定しなくても開発機の状態から隔離される。"""
    isolation.assert_development_state_isolated(tmp_path)


def test_import_time_paths_point_to_collection_home(
    pytestconfig: pytest.Config, host_environ: Callable[[], dict[str, str]]
) -> None:
    """収集時にimportした製品モジュールのホーム由来の定数は、実ホームを指さずpytestの一時領域を指す。

    導入処理の試験がCLIを模擬しても、製品が直接読む登録ファイル（`INSTALLED_PLUGINS_PATH`）が実ホームを指すと、
    開発機の導入状態で結果が変わる。
    """
    collection_home = pathlib.Path(pytestconfig.option.basetemp) / "import-time-home"
    host_home = pathlib.Path(host_environ()["USERPROFILE" if sys.platform == "win32" else "HOME"])
    update_log = post_apply._UPDATE_LOG_PATH  # noqa: SLF001  # pylint: disable=protected-access
    for path in (claude_common.CLAUDE_HOME, claude_common.INSTALLED_PLUGINS_PATH, update_log, sync_report.REPORT_PATH):
        assert path.is_relative_to(collection_home), path
    assert host_home / ".claude" != claude_common.CLAUDE_HOME


@pytest.mark.parametrize("scope", [None, "session"])
def test_async_operations_end_before_next_test_environment(
    tmp_path: pathlib.Path, scope: str | None, record_property: Callable[[str, object], None]
) -> None:
    """実設定で非同期処理の波及を防ぎ、sessionへ戻した対照では同じ波及を検出する。"""
    result = loop_isolation_support.assert_loop_scope_isolation(
        tmp_path, configuration=pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml", scope=scope
    )
    record_property("loop_scope_observation", result.stdout + result.stderr)
