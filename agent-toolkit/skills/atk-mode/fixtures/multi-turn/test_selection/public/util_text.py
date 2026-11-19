"""文字列の補助処理。"""


def slugify(text: str) -> str:
    """英数字とハイフンだけの識別子へ変換する。"""
    return "-".join(part for part in "".join(c.lower() if c.isalnum() else " " for c in text).split())
