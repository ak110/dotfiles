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
_DESIGN_QUOTE = (
    "atk serveの画面、さっき言ったラベルの話だけじゃなく、余白の統一感とか"
    "その他もろもろデザインの基本が全然できてない気がする。もろもろ全体的にちゃんとやれるように改善して。"
)
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


@pytest.mark.parametrize("second_marker", ["3.", "4.", "9."])
def test_preserve_the_start_number_of_a_markdown_ordered_list(second_marker: str) -> None:
    original = f"## 完成条件\n\n3. 第三の条件。\n{second_marker} 第四の条件。\n"
    table = _TABLE.replace(
        f"| {_FIRST} | 1 | 補足条件 | 先頭の文。<br>末尾も保つ。 | 完成条件 1 |",
        f"| {_FIRST} | 3 | 補足条件 | 第三の条件。 | 完成条件 3 |\n"
        f"| {_FIRST} | 4 | 補足条件 | {second_marker} 第四の条件。 | 完成条件 4 |",
    )
    required = {**_REQUIRED, _FIRST: (2, 1)}
    originals = {**_ORIGINALS, _FIRST: original}
    review_contract.validate(table, required, originals=originals)
    with pytest.raises(ValueError, match="補足条件 4 が原本の全文と一致しない"):
        review_contract.validate(table.replace("第四の条件。", "省略。"), required, originals=originals)


def test_original_request_cannot_be_replaced_by_the_completion_conditions() -> None:
    originals = {
        **_ORIGINALS,
        _FIRST: _ORIGINALS[_FIRST] + f"\n## ユーザー指摘の逐語引用\n\n> {_DESIGN_QUOTE}\n",
    }
    with pytest.raises(ValueError, match="引用1"):
        review_contract.validate(_TABLE, _REQUIRED, originals=originals)


@pytest.mark.parametrize(
    ("evidence", "valid"),
    [
        ("", False),
        (f"{_FIRST} 引用照合1: 未達 — 根拠: 一覧以外の画面が小さく読みにくい", False),
        (f"{_FIRST} 引用照合1: 達成 — 根拠: 原文要求1・原文要求2", False),
        (f"{_FIRST} 引用照合1: 達成 — 根拠: 原文要求1", False),
        (f"{_FIRST} 引用照合1: 除外 — 根拠: 割当外 — 20261125-030100-002.md#反映内容と反映先", False),
    ],
)
def test_quote_receipt_requires_live_original_requirement(evidence: str, valid: bool) -> None:
    originals = {_FIRST: f"## ユーザー指摘の逐語引用\n\n> {_DESIGN_QUOTE}\n"}
    result = (
        f"{_FIRST} 完成条件1: 達成 — 根拠: 画面はすべて共通部品を使っている\n"
        f"{_FIRST} 原文要求1「もろもろ全体的にちゃんとやれるように改善して。」: 達成 — 根拠: 実機で全画面が読めた\n"
        f"{evidence}\n"
    )
    if valid:
        review_contract.validate_origin_verdicts(result, originals)
    else:
        with pytest.raises(ValueError, match="引用照合1"):
            review_contract.validate_origin_verdicts(result, originals)


@pytest.mark.parametrize("heading", ["ユーザー指摘の逐語引用", "ユーザーコメント", "回答"])
@pytest.mark.parametrize("second", ["達成", "未達", "根拠不足", "欠落", "割当外"])
def test_two_requests_in_one_passage_are_checked_against_original(heading: str, second: str) -> None:
    original = f"## {heading}\n\n> 受入時に保存を実行する。受入時に再読込を実行する。\n"
    first = f"{_FIRST} 原文要求1「受入時に保存を実行する。」: 達成 — 根拠: 保存は成功"
    lines = [f"{_FIRST} 完成条件1: 達成 — 根拠: 保存済み", first]
    cited = "原文要求1"
    if second == "割当外":
        lines.append(f"{_FIRST} 引用除外1.2: 割当外 — 根拠: {_SECOND}#反映内容と反映先")
    elif second != "欠落":
        lines.append(f"{_FIRST} 原文要求2「受入時に再読込を実行する。」: {second} — 根拠: 再読込の観測")
        if second == "達成":
            cited += "・原文要求2"
    lines.append(f"{_FIRST} 引用照合1: 達成 — 根拠: {cited}")
    if second in {"達成", "割当外"}:
        review_contract.validate_origin_verdicts("\n".join(lines), {_FIRST: original})
    else:
        with pytest.raises(ValueError, match="引用照合1.2"):
            review_contract.validate_origin_verdicts("\n".join(lines), {_FIRST: original})
