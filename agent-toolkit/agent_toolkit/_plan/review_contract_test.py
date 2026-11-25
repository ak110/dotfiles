import pathlib

import pytest

from agent_toolkit._plan import review_contract

_FIRST = "20261125-030100-001.md"
_SECOND = "20261125-030100-002.md"
_TABLE = f"""
| WI | 条件番号 | 区分 | 内容 | 出典 |
| --- | --- | --- | --- | --- |
| {_FIRST} | 1 | 補足条件 | 先頭の文。<br>末尾も保つ。 | 完成条件 1 |
| {_FIRST} | 2 | 主要完成状態 | 変更する。 | 反映内容1 |
| {_SECOND} | 1 | 補足条件 | 比較する。 | 完成条件 1 |
| {_SECOND} | 2 | 主要完成状態 | 変更する。 | 反映内容1 |
"""
_REQUIRED = {_FIRST: (1, 1), _SECOND: (1, 1)}
_ORIGINALS = {
    _FIRST: "## 完成条件\n\n1. 先頭の文。\n   末尾も保つ。\n",
    _SECOND: "## 完成条件\n\n1. 比較する。\n",
}


def test_numbered_conditions_preserve_multiline_content_for_each_wi() -> None:
    review_contract.validate(_TABLE, _REQUIRED, originals=_ORIGINALS)


def test_numbered_condition_rejects_truncation_with_same_count_and_number() -> None:
    with pytest.raises(ValueError, match="補足条件 1 が原本の全文と一致しない"):
        review_contract.validate(_TABLE.replace("<br>末尾も保つ。", ""), _REQUIRED, originals=_ORIGINALS)


def test_numbered_conditions_reject_extra_requirement() -> None:
    table = _TABLE + f"| {_SECOND} | 3 | 補足条件 | 比較候補を実装する。 | 背景 |\n"
    with pytest.raises(ValueError, match="原本にない補足条件 3"):
        review_contract.validate(table, _REQUIRED, originals=_ORIGINALS)


def test_background_cannot_create_an_implementation_obligation() -> None:
    table = _TABLE + f"| {_SECOND} | 3 | 主要完成状態 | 比較候補を実装する。 | 調査結果と判断根拠の比較候補 |\n"
    with pytest.raises(ValueError, match="実装義務の出典"):
        review_contract.validate(table, _REQUIRED, originals=_ORIGINALS)


def test_explicit_non_adoption_is_not_a_major_requirement() -> None:
    original = {
        **_ORIGINALS,
        _SECOND: _ORIGINALS[_SECOND] + "\n## 反映内容と反映先\n\n1. 変更する。\n\n採らないもの:\n\n- 新しい基盤を作る。\n",
    }
    required = {**_REQUIRED, _SECOND: (1, 2)}
    table = _TABLE + f"| {_SECOND} | 3 | 主要完成状態 | 新しい基盤を作る。 | 反映内容2 |\n"
    with pytest.raises(ValueError, match="実装義務の出典"):
        review_contract.validate(table, required, originals=original)
    review_contract.validate(table.replace("新しい基盤を作る。", "対象外: 新しい基盤は不要"), required, originals=original)


def test_numbered_reference_must_name_the_registered_original_and_item(tmp_path: pathlib.Path) -> None:
    source = tmp_path / _SECOND
    paths = {_SECOND: source}
    table = _TABLE.replace("比較する。", f"参照: `{source}#完成条件1`")
    review_contract.validate(table, _REQUIRED, originals=_ORIGINALS, original_paths=paths)
    review_contract.validate(
        table.replace(f"参照: `{source}#完成条件1`", f"{source}「完成条件」項目1"),
        _REQUIRED,
        originals=_ORIGINALS,
        original_paths=paths,
    )
    with pytest.raises(ValueError, match="原本の全文と一致しない"):
        review_contract.validate(
            table.replace("#完成条件1", "#完成条件2"), _REQUIRED, originals=_ORIGINALS, original_paths=paths
        )


def test_automatically_numbered_conditions_follow_item_order() -> None:
    originals = {**_ORIGINALS, _FIRST: _ORIGINALS[_FIRST] + "1. 後続の文。\n   最後まで保つ。\n"}
    table = _TABLE.replace(
        f"| {_FIRST} | 2 | 主要完成状態 |",
        f"| {_FIRST} | 2 | 補足条件 | 1. 後続の文。<br>最後まで保つ。 | 完成条件 2 |\n| {_FIRST} | 3 | 主要完成状態 |",
    )
    required = {**_REQUIRED, _FIRST: (2, 1)}
    review_contract.validate(table, required, originals=originals)

    with pytest.raises(ValueError, match="補足条件 2 が原本の全文と一致しない"):
        review_contract.validate(table.replace("<br>最後まで保つ。", ""), required, originals=originals)
