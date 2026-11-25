import markdown_it
from markdown_it.token import Token

_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})
_LITERAL_BLOCK_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": True, "linkify": False})


def normalize_newlines(text: str) -> str:
    """見出しの行位置を安定させるため、改行をLFへそろえる。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def without_literal_blocks(text: str) -> str:
    normalized = normalize_newlines(text.removeprefix("\ufeff"))
    lines = normalized.split("\n")
    for token in _LITERAL_BLOCK_MARKDOWN.parse(normalized):
        if token.type in {"fence", "code_block", "blockquote_open", "html_block"} and token.map is not None:
            start, end = token.map
            lines[start:end] = [""] * (end - start)
    return "\n".join(lines)


def parse_headings(text: str, level: int) -> list[tuple[Token, str]]:
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
    return [
        (token, content) for token, content in parse_headings(text, level) if token.level == 0 and token.markup == "#" * level
    ]


def h2_section_bodies(text: str) -> list[tuple[str, list[str]]]:
    normalized = normalize_newlines(text)
    lines = normalized.split("\n")

    toplevel = top_level_atx_headings(normalized, 2)
    sections: list[tuple[str, list[str]]] = []
    for index, (token, content) in enumerate(toplevel):
        assert token.map is not None
        next_token = toplevel[index + 1][0] if index + 1 < len(toplevel) else None
        end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
        sections.append((content.strip(), lines[token.map[1] : end]))
    return sections


def h2_sections(text: str) -> list[tuple[str, bool]]:
    return [(name, any(line.strip() for line in body)) for name, body in h2_section_bodies(text)]


def last_h2_section_lines(text: str, name: str) -> list[str] | None:
    bodies = [body for heading, body in h2_section_bodies(text) if heading == name]
    return bodies[-1] if bodies else None


def last_h2_section_items(text: str, name: str) -> list[str]:
    lines = last_h2_section_lines(text, name) or []
    tokens = markdown_it.MarkdownIt().parse("\n".join(lines))
    items = [token for token in tokens if token.type == "list_item_open" and token.level == 1]
    if not items:
        items = [token for token in tokens if token.type == "paragraph_open" and token.level == 0]
    return ["\n".join(lines[token.map[0] : token.map[1]]).strip() for token in items if token.map is not None]
