"""各要素の条件から金額を返す課題。"""


def calculate(amount: int, premium: bool, exempt: bool) -> int:
    """割引の後に手数料を適用する。"""
    discounted = amount * 9 // 10 if premium else amount
    return discounted if exempt else discounted + 20


def batch(items: list[dict]) -> list[int]:
    """一括処理の結果を入力順で返す。"""
    if not items:
        return []
    first = items[0]
    return [calculate(item["amount"], first["premium"], first["exempt"]) for item in items]
