"""要求を書く節（通常AWIの本文と、計画ファイルの要求を書く部分）の見出しの検査。

節の構成は`agent-toolkit:atk-mode/references/awi-body.md`「通常AWIの本文」が定める。
`atk wi add`・`atk wi edit`と計画の構造の検査が、同じ定義と同じ関数で判定する。
"""

from __future__ import annotations

from agent_toolkit._common import markdown_headings
from agent_toolkit._plan.structure.constants import (
    REQUEST_H2_LEGACY,
    REQUEST_H2_ORDER,
    REQUEST_H2_REQUIRED,
    REQUEST_H2_USER_COMMENT,
)

_READ_ORDER: tuple[str, ...] = (
    *REQUEST_H2_ORDER[:2],
    *REQUEST_H2_LEGACY,
    *REQUEST_H2_ORDER[2:],
)
"""順序の判定に使う並び。保存済みの旧形式AWIが持つ節を、当時の位置（`原因分析`の後ろ）で受け付ける。"""


def request_sections(text: str) -> list[tuple[str, bool]]:
    """本文のトップレベルH2を、見出し名と本文が非空かの対で出現順に返す。"""
    return markdown_headings.h2_sections(text)


def request_section_problems(sections: list[tuple[str, bool]]) -> list[str]:
    """要求を書く節の必須節の欠落、置いた節の空の本文、順序の不一致を、問題ごとの文で返す。

    `sections`は検査する範囲のH2だけを渡す。計画では、要求を書く節以外の固定H2を除いて渡す。
    """
    filled = {name for name, has_body in sections if has_body}
    appeared = [name for name, _has_body in sections if name in _READ_ORDER]
    problems: list[str] = []
    if missing := [name for name in REQUEST_H2_REQUIRED if name not in filled]:
        problems.append(f"非空の必須節がありません: {'、'.join(missing)}")
    if empty := [
        name for name, has_body in sections if not has_body and name not in (*REQUEST_H2_REQUIRED, REQUEST_H2_USER_COMMENT)
    ]:
        problems.append(f"本文が空の節があります: {'、'.join(empty)}")
    order_indexes = [_READ_ORDER.index(name) for name in appeared]
    if order_indexes != sorted(order_indexes):
        problems.append(f"H2の順序が規定と異なります: {'、'.join(appeared)}")
    return problems
