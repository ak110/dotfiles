"""本文を保存する`atk`サブコマンドに共通する一致判定。

保存経路ごとに比較処理を複製すると、正規化範囲や不一致時の文言が分岐するため、
末尾改行だけをそろえる判定を本モジュールへ集約する。
"""


def normalize(text: str) -> str:
    """末尾改行の有無だけをそろえた比較用の本文を返す。"""
    return text[:-1] if text.endswith("\n") else text


def first_difference(expected: str, saved: str) -> int | None:
    """正規化した本文が最初に異なる位置を1起点で返す。"""
    expected_normalized = normalize(expected)
    saved_normalized = normalize(saved)
    if expected_normalized == saved_normalized:
        return None
    limit = min(len(expected_normalized), len(saved_normalized))
    position = next(
        (index for index in range(limit) if expected_normalized[index] != saved_normalized[index]),
        limit,
    )
    return position + 1


def verdict(expected: str, saved: str) -> str:
    """正規化した本文の一致又は最初の差異位置を固定文言で返す。"""
    position = first_difference(expected, saved)
    if position is None:
        return "一致"
    return f"不一致（最初の差異: {position}文字目）"
