"""依頼ごとに選ぶスキルを返す課題。"""


def select(request_id: str) -> dict[str, list[str]]:
    """依頼IDに対し、初期選択と本文を読んだ後の追加選択の識別子を返す。"""
    del request_id
    return {"initial": [], "additional": []}
