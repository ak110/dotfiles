"""WIの要求抽出における文字エンコーディングの境界を検証する。"""

import pytest

from agent_toolkit._common import requirement_units


@pytest.mark.parametrize("kind", ["awi", "uwi"])
def test_expected_rows_accepts_bom_at_wi_body_start(kind: str) -> None:
    """show形式に埋め込まれたWIの先頭BOMだけを除き、本文中の文字を保持する。"""
    filename = "20260101-000000-001.md"
    body = "## 完成条件\n\n- 表\ufeff題を表示する。\n" if kind == "awi" else "## 回答\n\n表\ufeff題を表示する。\n"
    text = f"---\ntype: {kind}\nsource: session-review\n---\n\n{body}"
    heading = f"### {filename} [inbox]\n"
    expected = requirement_units.expected_rows(heading + text, filename)
    assert requirement_units.expected_rows(heading + "\ufeff" + text, filename) == expected
    conditions, requirements = expected
    assert "表\ufeff題を表示する。" in [*conditions, *(unit for unit, _origin in requirements)]
