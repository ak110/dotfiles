import pathlib

import pytest

from agent_toolkit._common.external_source_review import COLUMNS, table_error

_URL = "https://example.com/reference"


@pytest.mark.parametrize(
    ("reference", "valid"),
    [("`docs/rule.md:2`", True), ("docs/rule.md", False), ("docs/rule.md:3", False), ("../rule.md:1", False)],
)
def test_no_difference_requires_a_real_reference(tmp_path: pathlib.Path, reference: str, valid: bool) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "rule.md").write_text("見出し\n本文\n", encoding="utf-8")
    table = "| " + " | ".join(COLUMNS) + " |\n"
    table += "| " + " | ".join(["---"] * len(COLUMNS)) + " |\n"
    table += f"| {_URL} | 原文 | {reference} | なし | 現行の節へ統合 | 採用 |\n"
    error = table_error(table, {_URL}, tmp_path)
    assert (error is None) == valid
    if not valid:
        assert error is not None
        assert "3行目" in error


def test_legacy_table_and_a_table_for_a_different_case_are_rejected(tmp_path: pathlib.Path) -> None:
    old = f"| 資料URL | 採否と理由 |\n| --- | --- |\n| {_URL} | 採用 |\n| {_URL} | 保留 |\n| {_URL} | 不採用 |\n"
    error = table_error(old, {_URL}, tmp_path)
    assert error is not None
    assert "資料の主張" in error
    assert all(f"{line}行目" in error for line in (3, 4, 5))
    assert "既存との差分" in error
    assert "`path:行`" in error and "同じ行動" in error

    other = "| " + " | ".join(COLUMNS) + " |\n"
    other += "| " + " | ".join(["---"] * len(COLUMNS)) + " |\n"
    other += f"| {_URL} | 原文 | 既存 | 差 | 統合 | 採用 |\n"
    error = table_error(other, {_URL + "/other"}, tmp_path)
    assert error is not None
    assert _URL + "/other" in error
