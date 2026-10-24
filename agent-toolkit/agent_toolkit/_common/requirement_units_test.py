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


def test_expected_rows_keeps_terminal_user_comment_after_result_boundary() -> None:
    """sourceも完成条件もないAWIでは、処理結果後の末尾コメントも本文要求として数える。"""
    filename = "20260101-000000-001.md"
    output = (
        f"### {filename} [processing]\n---\ntype: awi\n---\n\n原要求。\n\n"
        "## 処理結果\n\n処理メモ。\n\n## ユーザーコメント\n\n追加要求。\n"
    )

    conditions, requirements = requirement_units.expected_rows(output, filename)

    assert not conditions
    assert requirements == [("原要求。", f"{filename}#本文"), ("追加要求。", f"{filename}#本文")]


def test_expected_rows_does_not_count_user_comment_twice_before_result_boundary() -> None:
    """処理結果より前の末尾コメントは本文要求へ一度だけ含める。"""
    filename = "20260101-000000-001.md"
    output = (
        f"### {filename} [processing]\n---\ntype: awi\n---\n\n原要求。\n\n"
        "## ユーザーコメント\n\n追加要求。\n\n## 処理結果\n\n処理メモ。\n"
    )

    _conditions, requirements = requirement_units.expected_rows(output, filename)

    assert requirements == [("原要求。", f"{filename}#本文"), ("追加要求。", f"{filename}#本文")]


def test_expected_rows_keeps_existing_raw_awi_rows_without_result_boundary() -> None:
    """処理結果境界のない既存AWIは従来どおり本文だけを要求として返す。"""
    filename = "20260101-000000-001.md"
    output = f"### {filename} [inbox]\n---\ntype: awi\n---\n\n既存要求。\n"

    assert requirement_units.expected_rows(output, filename) == ([], [("既存要求。", f"{filename}#本文")])


def test_expected_rows_keeps_user_comment_between_record_boundary_and_adoption_record() -> None:
    """処理記録の境界の後ろにあり、後ろに採否記録が続くコメントも本文要求として数える。"""
    filename = "20260101-000000-001.md"
    output = (
        f"### {filename} [processing]\n---\ntype: awi\n---\n\n原要求。\n\n"
        "## 処理結果\n\n処理メモ。\n\n## ユーザーコメント\n\n追加要求。\n\n"
        "## 処理結果\n\n- 採否: adopted\n"
    )

    _conditions, requirements = requirement_units.expected_rows(output, filename)

    assert requirements == [("原要求。", f"{filename}#本文"), ("追加要求。", f"{filename}#本文")]
