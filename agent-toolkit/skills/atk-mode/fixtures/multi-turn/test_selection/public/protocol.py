"""送受信する値の符号化。"""


def encode(value: dict) -> str:
    """辞書をキー順の`key=value`列へ符号化する。"""
    return ";".join(f"{key}={value[key]}" for key in sorted(value))
