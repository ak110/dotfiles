"""計画の見出し名と構造定数の一致を確かめる。"""

import pathlib

import pytest

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._testing.normative_contract import read_contract

_PLAN_FILE_STANDARDS = pathlib.Path(__file__).resolve().with_name("plan-file-standards.md")


@pytest.mark.parametrize(
    "expected",
    [
        *(f"`## {name}`" for name in _plan_format.PLAN_SINGLE_FILE_H2_ORDER),
        "`## 設計・実装方針`",
        *(f"`### {name}`" for name in _plan_format.PLAN_PERMANENCE_H3),
        f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}<1から始まる連番>`",
    ],
)
def test_plan_file_standards_states_every_structure_constant(expected: str) -> None:
    """構造の判定に用いる見出し名を`plan-file-standards.md`の本文が明記する。

    実装だけが要件を持つ状態を避け、構造定数を改訂した場合に`plan-file-standards.md`が更新されていないことを検出する。
    """
    text = read_contract(
        _PLAN_FILE_STANDARDS,
        _PLAN_FILE_STANDARDS.parents[3],
        ("plan-file-standards-structure.md", "計画ファイルの構成"),
        ("plan-file-standards-design.md", "恒久化"),
    )
    assert expected in text
