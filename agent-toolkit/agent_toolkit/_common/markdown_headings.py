"""Markdown構文上の見出しを、コードフェンスの外から抽出する。"""

import markdown_it
from markdown_it.token import Token

_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})


def normalize_newlines(text: str) -> str:
    """見出しの行位置を安定させるため、改行をLFへそろえる。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_headings(text: str, level: int) -> list[tuple[Token, str]]:
    """指定レベルの見出しの開きtokenと本文を、出現順で返す。"""
    tokens = _MARKDOWN.parse(normalize_newlines(text))
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
