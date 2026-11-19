"""検証の状態を分類する課題。"""


def classify(status: str) -> str:
    """検証の状態から報告区分を返す。"""
    return "不合格" if status == "failed" else "合格"
