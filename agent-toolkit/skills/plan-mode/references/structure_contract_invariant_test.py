"""計画の見出し名と構造定数の一致を確かめる。"""

import pathlib

import pytest

from agent_toolkit._plan import structure as _plan_format

_PLAN_FILE_STANDARDS = pathlib.Path(__file__).resolve().with_name("plan-file-standards.md")


@pytest.mark.parametrize(
    "expected",
    [
        *(f"`## {name}`" for name in _plan_format.PLAN_SINGLE_FILE_H2_ORDER),
        *(f"`### {name}`" for name in _plan_format.PLAN_PERMANENCE_H3),
        f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}<1から始まる連番>`",
    ],
)
def test_plan_file_standards_states_every_structure_constant(expected: str) -> None:
    """構造の判定に用いる見出し名を`plan-file-standards.md`の本文が明記する。

    実装だけが要件を持つ状態を避け、構造定数を改訂した場合に`plan-file-standards.md`が更新されていないことを検出する。
    """
    assert expected in _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")


@pytest.mark.parametrize("heading", list(_plan_format.PLAN_WI_MACHINE_DETECTABLE_ORIGIN_HEADINGS))
def test_dialog_origin_clause_lists_machine_detectable_headings(heading: str) -> None:
    """`[対話由来]`注記を定める文が、計画構造の判定が人間由来を読み取る見出しを全て列挙する。

    条文から列挙が消えると、計画の起草者は注記の要否を条文から判定できず、`plan-create`の拒否で初めて知る。
    同じ見出しは質問の記入を扱う別の文にも現れるため、見出しを探す範囲を`[対話由来]`を含む文に限る。
    """
    text = _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")
    sentences = [sentence for line in text.splitlines() for sentence in line.split("。") if "`[対話由来]`" in sentence]
    assert sentences
    assert any(f"`## {heading}`" in sentence for sentence in sentences)
