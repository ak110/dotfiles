"""画面へ渡す表示文字列を返す課題。"""

FONT_SIZE = 12


def render(text: str) -> str:
    """表示文字列を返す。"""
    return "".join(text.split())
