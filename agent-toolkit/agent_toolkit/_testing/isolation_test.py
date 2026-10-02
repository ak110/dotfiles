"""`agent_toolkit/`配下のテストへ開発機の状態からの隔離が自動で適用されることを確かめる。"""

import pathlib

from agent_toolkit._testing import isolation


def test_development_state_isolated_by_default(tmp_path: pathlib.Path) -> None:
    """この起動範囲のテストは、何も設定しなくても開発機の状態から隔離される。"""
    isolation.assert_development_state_isolated(tmp_path)
