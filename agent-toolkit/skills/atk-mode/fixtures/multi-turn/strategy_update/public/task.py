"""明細を集計する課題の初期実装。"""


def total_cents(records: list[dict]) -> int:
    """既存の整数金額を合計する。"""
    return sum(record["amount"] for record in records)
