"""原因分析の表の行名と計画の構造定数の一致を確かめる。"""

import pathlib

import pytest

from agent_toolkit._plan import structure as _plan_format

_ROOT_CAUSE_ANALYSIS = pathlib.Path(__file__).resolve().with_name("root-cause-analysis.md")


@pytest.mark.parametrize("row_name", _plan_format.PLAN_BUG_TABLE_ROWS)
def test_root_cause_analysis_states_every_bug_table_row(row_name: str) -> None:
    """調査表の固定行名を`agent-toolkit:bugfix`の`references/root-cause-analysis.md`の集約表が明記する。

    行名を構造定数で定めるため、集約表に更新されていない行名があれば検出する。
    """
    assert f"| {row_name} | " in _ROOT_CAUSE_ANALYSIS.read_text(encoding="utf-8")
