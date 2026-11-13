"""読込表の操作宣言の欠落と、理由付き対象外の区別を検証する。"""

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._common.read_triggers_operations import missing_operation_rows


@pytest.mark.parametrize(
    "condition,operation",
    [
        ("委譲先の返却を受領する前", "delegation-result"),
        ("担当を起動する前", "delegation"),
        ("ユーザーへの確認を発行する前", "confirmation"),
        ("WIを保存する前", "wi-standard-commit"),
    ],
)
def test_missing_operation_declarations_and_reasoned_exemption(tmp_path, condition, operation):
    """操作列を外した4区分を検出し、理由付き対象外は判定から除く。"""
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    target.write_text("# 判断基準\n", encoding="utf-8")

    def parse(body):
        source.write_text(body, encoding="utf-8")
        return read_triggers.parse_tables(source, tmp_path)

    bare = "| 時点または条件 | 全文読む資料 |\n| --- | --- |\n"
    assert missing_operation_rows(parse(bare + f"| {condition} | `target.md` |\n"))
    assert missing_operation_rows(parse(bare + f"| {condition}（操作対象外:   ） | `target.md` |\n"))
    marked = condition + "（操作対象外: 入力の分類だけを行う判断であり、操作の実行時点ではない）"
    assert not missing_operation_rows(parse(bare + f"| {marked} | `target.md` |\n"))
    explicit = "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
    assert not missing_operation_rows(parse(explicit + f"| {condition} | `target.md` | 操作 | `{operation}` |\n"))
