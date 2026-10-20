"""スキル配下のテストへ開発機の状態からの隔離が自動で適用されることを確かめる。"""

import pathlib
from collections.abc import Callable

import pytest

from agent_toolkit._testing import isolation, loop_isolation_support


def test_development_state_isolated_by_default(tmp_path: pathlib.Path) -> None:
    """この起動範囲のテストは、何も設定しなくても開発機の状態から隔離される。"""
    isolation.assert_development_state_isolated(tmp_path)


@pytest.mark.parametrize("scope", [None, "session"])
def test_async_operations_end_before_next_test_environment(
    tmp_path: pathlib.Path, scope: str | None, record_property: Callable[[str, object], None]
) -> None:
    """実設定で非同期処理の波及を防ぎ、sessionへ戻した対照では同じ波及を検出する。"""
    result = loop_isolation_support.assert_loop_scope_isolation(
        tmp_path, configuration=pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml", scope=scope
    )
    record_property("loop_scope_observation", result.stdout + result.stderr)
