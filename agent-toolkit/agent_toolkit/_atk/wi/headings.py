"""キュー項目の本文からコードフェンス外のH2見出しを抽出する。

`atk wi add`の必須節の有無の判定と`## ユーザーコメント`節の探索は、いずれもコードフェンスの
内側にある`## `で始まる行を見出しとして数えない判定を要する。判定を1箇所へ集約し、
呼び出し元ごとに異なる方法で走査しないようにする。

行番号はmarkdown-itが数える単位に合わせるため、走査の前に改行をLFへ正規化する。
呼び出し側が行を切り出す場合も、同じ正規化を経た本文へ`token.map`を適用する。
"""

from markdown_it.token import Token

from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._common import markdown_headings
from agent_toolkit._common.markdown_headings import last_h2_section_items, last_h2_section_lines, parse_headings


def parse_h2_headings(text: str) -> list[tuple[Token, str]]:
    return parse_headings(frontmatter.normalize_newlines(text), 2)


def contains_h2(text: str) -> bool:
    return bool(parse_h2_headings(text))


def h2_sections(text: str) -> list[tuple[str, bool]]:
    return markdown_headings.h2_sections(text)


__all__ = ["contains_h2", "h2_sections", "last_h2_section_items", "last_h2_section_lines", "parse_h2_headings"]
