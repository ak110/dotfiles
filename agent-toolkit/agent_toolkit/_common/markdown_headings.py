"""Markdown構文上の見出しを、コードフェンスの外から抽出する。"""

import markdown_it
from markdown_it.token import Token

_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})


def normalize_newlines(text: str) -> str:
    """見出しの行位置を安定させるため、改行をLFへそろえる。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_headings(text: str, level: int) -> list[tuple[Token, str]]:
    """指定レベルの見出しの開きtokenと本文を、出現順で返す。"""
    tokens = _MARKDOWN.parse(normalize_newlines(text.removeprefix("\ufeff")))
    headings: list[tuple[Token, str]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != f"h{level}" or token.map is None:
            continue
        inline = tokens[index + 1]
        if inline.type == "inline":
            headings.append((token, inline.content))
    return headings


def top_level_atx_headings(text: str, level: int) -> list[tuple[Token, str]]:
    """トップレベルのATX見出しだけを返す。"""
    return [
        (token, content) for token, content in parse_headings(text, level) if token.level == 0 and token.markup == "#" * level
    ]


def h2_section_bodies(text: str) -> list[tuple[str, list[str]]]:
    """ATX記法のトップレベルH2について、見出し名と本文の行を出現順で返す。

    本文はその見出しの直後から次のH2の直前までとする。
    引用や箇条書きの内側にあるH2と、setext記法の見出しは対象にしない。
    """
    normalized = normalize_newlines(text)
    lines = normalized.split("\n")
    # 次の見出しまでを本文とするため、境界の探索もトップレベルのATX H2だけに限る。
    # 引用や箇条書きの内側にあるH2を境界へ含めると、節の本文の範囲がその行で終わり、非空の判定を誤る。
    toplevel = top_level_atx_headings(normalized, 2)
    sections: list[tuple[str, list[str]]] = []
    for index, (token, content) in enumerate(toplevel):
        assert token.map is not None
        next_token = toplevel[index + 1][0] if index + 1 < len(toplevel) else None
        end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
        sections.append((content.strip(), lines[token.map[1] : end]))
    return sections


def h2_sections(text: str) -> list[tuple[str, bool]]:
    """ATX記法のトップレベルH2について、見出し名と本文が非空かの対を出現順で返す。

    空白だけの行を非空として数えない。
    固定書式の節があるかを判定する際に、引用・箇条書きの内側のH2とsetext記法の見出しを節として数えないためである。
    """
    return [(name, any(line.strip() for line in body)) for name, body in h2_section_bodies(text)]
