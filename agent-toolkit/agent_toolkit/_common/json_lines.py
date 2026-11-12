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
    記録と誤認しないためである。断片のUTF-8の破損は判定を妨げない。
    手前の断片が単独で成立する場合は完全な記録を捨てることになるため、連結とみなさず`None`を返す。

    行全体の構文エラーが行末で起きる行は、末尾が欠けただけの1記録と、断片が`:`や`[`の直後で途切れて
    次の記録が続いた連結行の両方でありうる。前者では入れ子の`type`付きobject（content blockなど）が
    行末まで成立するため、この形では後半が記録の最上位にだけ現れる`sessionId`も持つ場合に限り連結とする。
    hookは記録の全行を読むため、候補ごとに行の残りを切り出さず、候補の位置から1つの値だけを解析する。
    """
    # 断片の不正なバイトを1文字ずつに写し、文字位置とバイト位置の対応を保ったまま走査する。
    text = line.decode("utf-8", "surrogateescape")
    content_end = len(text.rstrip(_JSON_WHITESPACE))
    try:
        json.loads(text)
    except json.JSONDecodeError as e:
        required_keys = ("type", "sessionId") if e.pos >= content_end else ("type",)
    except RecursionError:
        return None
    else:
        return None
    start = text.find('{"', 1)
    while start >= 0:
        try:
            tail, end = _DECODER.raw_decode(text, start)
        except ValueError, RecursionError:
            tail, end = None, start
        if isinstance(tail, dict) and end >= content_end and all(key in tail for key in required_keys):
            byte_start = len(text[:start].encode("utf-8", "surrogateescape"))
            # 後半はUTF-8として正しい記録に限る。写した不正なバイトを含む後半は記録と扱わない。
            if _is_json(line[byte_start:]):
                return None if _is_json(line[:byte_start]) else (byte_start, tail)
        start = text.find('{"', start + 1)
    return None


_DECODER = json.JSONDecoder()
_JSON_WHITESPACE = " \t\n\r"


def _is_json(data: bytes) -> bool:
    try:
        json.loads(data)
    except ValueError, RecursionError:
        return False
    return True
