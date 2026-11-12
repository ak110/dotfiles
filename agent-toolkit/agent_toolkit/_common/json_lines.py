"""JSON Linesの物理行をLFだけで分ける。解析と空行の採否は呼び出し側が所有する。"""

import json
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


def split_joined_line(line: bytes) -> tuple[int, dict[str, typing.Any]] | None:
    """途中で途切れた記録の断片の直後へ、次の記録が改行なしで続く1行を分ける。

    Claude Codeのセッション記録で、書込みの途中で途切れた断片に完全な記録が連結された行を観測した。
    行の途中の`{"`から行末までが単独でJSONのobjectとして成立し、`type`キーを持つ最も早い位置を境界とし、
    境界のバイト位置と後半の記録を返す。`type`を要件にするのは、途切れた文字列の中のJSONらしい文字列を
    記録と誤認しないためである。判定はバイト列で行い、断片のUTF-8の破損は判定を妨げない。
    手前の断片が単独で成立する場合は完全な記録を捨てることになるため、連結とみなさず`None`を返す。
    """
    start = line.find(b'{"', 1)
    while start >= 0:
        try:
            tail = json.loads(line[start:].decode("utf-8"))
        except ValueError:
            tail = None
        if isinstance(tail, dict) and "type" in tail:
            try:
                json.loads(line[:start].decode("utf-8"))
            except ValueError:
                return start, tail
            return None
        start = line.find(b'{"', start + 1)
    return None
