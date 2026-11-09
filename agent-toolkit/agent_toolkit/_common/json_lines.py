"""JSON Linesの物理行をLFだけで分ける。解析と空行の採否は呼び出し側が所有する。"""

import typing


@typing.overload
def split_lines(text: str) -> list[str]: ...


@typing.overload
def split_lines(text: bytes) -> list[bytes]: ...


def split_lines(text: str | bytes) -> list[str] | list[bytes]:
    """LFで区切り、行末CRと最後のLFに由来する空要素だけを取り除く。

    JSON文字列中にそのまま含められるU+2028・U+2029・NELは区切りにしない。
    空入力は空列、途中の空行は空要素として返す。
    """
    if not text:
        return []
    if isinstance(text, bytes):
        byte_lines = text.split(b"\n")
        if byte_lines[-1] == b"":
            byte_lines.pop()
        return [line.removesuffix(b"\r") for line in byte_lines]
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]
