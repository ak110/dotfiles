"""session-reviewAWIのユーザーコメント節を抽出・更新する。"""

import _atk_wi_frontmatter as frontmatter
import markdown_it
from markdown_it.token import Token

_COMMENT_HEADING = "ユーザーコメント"
_MARKDOWN = markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})
_AGENT_USER_COMMENT_PREFIX = "ユーザーコメントはユーザーだけが書き込みます。"
AGENT_USER_COMMENT_ADD_ERROR = (
    _AGENT_USER_COMMENT_PREFIX + "エージェント環境から起動したatkでは、ユーザーコメント節を含む本文を投入できません。"
    "ユーザーの発言は本文中へ出所を示して引用してください。"
)
AGENT_USER_COMMENT_EDIT_ERROR = (
    _AGENT_USER_COMMENT_PREFIX + "エージェント環境から起動したatkでは、ユーザーコメント節を含む本文を指定できません。"
    "ユーザーコメント節は編集の対象から外し、保存済みの内容をそのまま残します。"
)


class UserCommentError(ValueError):
    """ユーザーコメント節の構造または入力が契約に適合しない。"""


def _body_parts(text: str) -> tuple[str, str]:
    """本文の前置き（frontmatter）とMarkdown本文を返す。"""
    parsed = frontmatter.parse_frontmatter(text)
    if parsed is None:
        return "", text
    body = parsed[1]
    return text[: len(text) - len(body)], body


def _lines(text: str) -> list[str]:
    """行末を保持した行列を返す。"""
    return text.splitlines(keepends=True)


def _is_blank_line(line: str) -> bool:
    """空行（空白だけの行を含む）か判定する。"""
    return not line.strip()


def _normalize_comment(text: str) -> str:
    """コメントの前後にある空行だけを除去し、本文を返す。"""
    lines = _lines(text)
    start = 0
    end = len(lines)
    while start < end and _is_blank_line(lines[start]):
        start += 1
    while end > start and _is_blank_line(lines[end - 1]):
        end -= 1
    return "".join(lines[start:end]).rstrip("\r\n")


def _heading_tokens(text: str) -> list[tuple[Token, str]]:
    """H2の開きtokenと見出し本文tokenを対応付けて返す。"""
    tokens = _MARKDOWN.parse(text)
    headings: list[tuple[Token, str]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or token.tag != "h2" or token.map is None:
            continue
        inline = tokens[index + 1]
        if inline.type != "inline":
            continue
        headings.append((token, inline.content))
    return headings


def _reserved_heading(token: Token, content: str) -> bool:
    """予約見出しのtokenか判定する。"""
    return token.level == 0 and token.markup == "##" and content == _COMMENT_HEADING


def _find_reserved_heading(body: str) -> Token | None:
    """予約見出しを検証し、置換対象のtokenを返す。"""
    headings = _heading_tokens(body)
    reserved = [token for token, content in headings if _reserved_heading(token, content)]
    if len(reserved) > 1:
        raise UserCommentError("ユーザーコメント節が複数あります")
    if not reserved:
        return None
    target = reserved[0]
    assert target.map is not None
    target_start = target.map[0]
    if any(token.map is not None and token.map[0] > target_start for token, _content in headings):
        raise UserCommentError("ユーザーコメント節の後ろに別のH2見出しがあります")
    return target


def extract_user_comment(text: str) -> str | None:
    """本文末尾の予約節からユーザーコメントを抽出する。"""
    _prefix, body = _body_parts(text)
    heading = _find_reserved_heading(body)
    if heading is None:
        return None
    lines = _lines(body)
    assert heading.map is not None
    return _normalize_comment("".join(lines[heading.map[1] :]))


def split_before_user_comment(text: str) -> tuple[str, str]:
    """本文を予約見出しの直前までと予約見出し以降へ分けて返す。"""
    prefix, body = _body_parts(text)
    heading = _find_reserved_heading(body)
    if heading is None:
        return text, ""
    lines = _lines(body)
    assert heading.map is not None
    return prefix + "".join(lines[: heading.map[0]]), "".join(lines[heading.map[0] :])


def has_reserved_heading(text: str) -> bool:
    """本文がコードフェンス外の予約見出しを含むか返す。"""
    _prefix, body = _body_parts(text)
    return any(_reserved_heading(token, content) for token, content in _heading_tokens(body))


def _validate_comment(text: str) -> str:
    """入力コメントを検証し、前後の空行を正規化する。"""
    normalized = _normalize_comment(text)
    if not normalized:
        raise UserCommentError("ユーザーコメントは空にできません")
    if any(token.type == "heading_open" and token.tag == "h2" for token in _MARKDOWN.parse(text)):
        raise UserCommentError("ユーザーコメントにコードフェンス外のH2見出しを含められません")
    return normalized


def update_user_comment(text: str, comment: str) -> str:
    """予約節を追記又は置換し、更新後の全文を返す。"""
    normalized = _validate_comment(comment)
    prefix, body = _body_parts(text)
    heading = _find_reserved_heading(body)
    if heading is None:
        base = text
        lines = _lines(base)
        end = len(lines)
        while end > 0 and _is_blank_line(lines[end - 1]):
            end -= 1
        base = "".join(lines[:end]).rstrip("\r\n")
        separator = "\n\n" if base else ""
        return f"{base}{separator}## {_COMMENT_HEADING}\n\n{normalized}\n"

    lines = _lines(body)
    assert heading.map is not None
    before_section = "".join(lines[: heading.map[1]]).rstrip("\r\n")
    return f"{prefix}{before_section}\n\n{normalized}\n"


__all__ = [
    "AGENT_USER_COMMENT_ADD_ERROR",
    "AGENT_USER_COMMENT_EDIT_ERROR",
    "UserCommentError",
    "extract_user_comment",
    "has_reserved_heading",
    "split_before_user_comment",
    "update_user_comment",
]
