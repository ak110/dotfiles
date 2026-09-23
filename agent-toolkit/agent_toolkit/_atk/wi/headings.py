"""キュー項目の本文からコードフェンス外のH2見出しを抽出する。

`atk wi add`の必須節検査と`## ユーザーコメント`節の探索は、いずれもコードフェンスの
内側にある`## `で始まる行を見出しとして数えない判定を要する。判定を1箇所へ集約し、
経路ごとに異なる走査が並存しないようにする。

行番号はmarkdown-itが数える単位に合わせるため、走査の前に改行をLFへ正規化する。
呼び出し側が行を切り出す場合も、同じ正規化を経た本文へ`token.map`を適用する。
"""

from markdown_it.token import Token

from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._common.markdown_headings import parse_headings, top_level_atx_headings


def parse_h2_headings(text: str) -> list[tuple[Token, str]]:
    """H2の開きtokenと見出し本文を、本文へ現れる順で対応付けて返す。"""
    return parse_headings(frontmatter.normalize_newlines(text), 2)


def contains_h2(text: str) -> bool:
    """コードフェンス外のH2見出しを含むか返す。"""
    return bool(parse_h2_headings(text))


def h2_sections(text: str) -> list[tuple[str, bool]]:
    """ATX記法のトップレベルH2について、見出し名と本文が非空かの対を出現順で返す。

    本文は当該見出しの直後から次のH2の直前までとし、空白だけの行を非空として数えない。
    引用や箇条書きの内側にあるH2と、setext記法の見出しは対象にしない。
    `## 実現性`のような固定書式の節の有無を判定する経路が、これらを節として数えないためである。
    """
    normalized = frontmatter.normalize_newlines(text)
    lines = normalized.split("\n")
    # 次の見出しまでを本文とするため、境界の探索もトップレベルのATX H2だけに限る。
    # 引用や箇条書きの内側にあるH2を境界へ含めると、節の本文の範囲が当該行で終わり、非空の判定を誤る。
    toplevel = top_level_atx_headings(normalized, 2)
    sections: list[tuple[str, bool]] = []
    for index, (token, content) in enumerate(toplevel):
        assert token.map is not None
        next_token = toplevel[index + 1][0] if index + 1 < len(toplevel) else None
        end = next_token.map[0] if next_token is not None and next_token.map is not None else len(lines)
        has_body = any(line.strip() for line in lines[token.map[1] : end])
        sections.append((content.strip(), has_body))
    return sections


__all__ = ["contains_h2", "h2_sections", "parse_h2_headings"]
