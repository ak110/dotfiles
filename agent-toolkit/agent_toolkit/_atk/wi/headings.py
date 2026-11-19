"""キュー項目の本文からコードフェンス外のH2見出しを抽出する。

`atk wi add`の必須節の有無の判定と`## ユーザーコメント`節の探索は、いずれもコードフェンスの
内側にある`## `で始まる行を見出しとして数えない判定を要する。判定を1箇所へ集約し、
呼び出し元ごとに異なる方法で走査しないようにする。

行番号はmarkdown-itが数える単位に合わせるため、走査の前に改行をLFへ正規化する。
呼び出し側が行を切り出す場合も、同じ正規化を経た本文へ`token.map`を適用する。
"""

from markdown_it import MarkdownIt
from markdown_it.token import Token

from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._common import markdown_headings
from agent_toolkit._common.markdown_headings import parse_headings


def parse_h2_headings(text: str) -> list[tuple[Token, str]]:
    """H2の開きtokenと見出し本文を、本文へ現れる順で対応付けて返す。"""
    return parse_headings(frontmatter.normalize_newlines(text), 2)


def contains_h2(text: str) -> bool:
    """コードフェンス外のH2見出しを含むか返す。"""
    return bool(parse_h2_headings(text))


def _h2_section_bodies(text: str) -> list[tuple[str, list[str]]]:
    """ATX記法のトップレベルH2について、見出し名と本文の行を出現順で返す。"""
    return markdown_headings.h2_section_bodies(text)


def h2_sections(text: str) -> list[tuple[str, bool]]:
    """ATX記法のトップレベルH2について、見出し名と本文が非空かの対を出現順で返す。"""
    return markdown_headings.h2_sections(text)


def last_h2_section_lines(text: str, name: str) -> list[str] | None:
    """見出し名が`name`のトップレベルH2のうち最後の節の本文の行を返す。該当する節が無ければ`None`を返す。

    同名の節を追記で重ねる記録（反映後の観測の再開記録など）は、最後の節を現在の値として読むためである。
    """
    bodies = [body for heading, body in _h2_section_bodies(text) if heading == name]
    return bodies[-1] if bodies else None


def last_h2_section_items(text: str, name: str) -> list[str]:
    """指定した最後のH2節の最上位リスト項目を全文で返し、リストが無ければ最上位段落を返す。

    起動時の被覆検査と条件表の生成が同じ項目番号を使うため、本文の区切りをここで共有する。
    項目内の複数段落や入れ子も、行範囲でまとめて保持する。
    """
    lines = last_h2_section_lines(text, name) or []
    tokens = MarkdownIt().parse("\n".join(lines))
    items = [token for token in tokens if token.type == "list_item_open" and token.level == 1]
    if not items:
        items = [token for token in tokens if token.type == "paragraph_open" and token.level == 0]
    return ["\n".join(lines[token.map[0] : token.map[1]]).strip() for token in items if token.map is not None]


__all__ = ["contains_h2", "h2_sections", "last_h2_section_items", "last_h2_section_lines", "parse_h2_headings"]
