"""`再発防止:`の行の先頭に置く対策の種類の語と、その判定。  pyfltr: allow-comment

語の定義元は`agent-basics:bugfix`の`references/prevention.md`「`再発防止:`の行」である。
AWIの原因分析の保存時の検査（`_plan/structure/sections_bugs.py`）と、ユーザーへ届く報告の検査
（`_hooks/norm_excuse_gate.py`）が同じ語を使うため、`_common`の1か所に置く。
"""

import re

ADOPTED_KINDS = ("原因の除去", "入力の制限", "判断の集約", "設計", "自動検出", "確認項目", "手順", "文書")

NOT_ADOPTED_KINDS = ("非欠陥", "処置済み", "被覆", "所有範囲外", "見送り")

UNRESOLVED_KIND = "引き継ぎ"

PREVENTION_KINDS = (*ADOPTED_KINDS, *NOT_ADOPTED_KINDS, UNRESOLVED_KIND)

# 語の直後を区切りに限り、「設計書」「文書化」のような語の一部の一致を除く。  pyfltr: allow-comment
_KIND_HEAD = re.compile(
    "(?:" + "|".join(re.escape(kind) for kind in sorted(PREVENTION_KINDS, key=len, reverse=True)) + r")(?=$|[\s:：、。,（(*_`])"
)
_LEADING_DECORATION = " \t　*_`~"

KINDS_GUIDANCE = (
    "採る策は"
    + "・".join(f"`{kind}`" for kind in ADOPTED_KINDS)
    + "、採らない場合は"
    + "・".join(f"`{kind}`" for kind in NOT_ADOPTED_KINDS)
    + f"、調査へ引き継ぐ場合は`{UNRESOLVED_KIND}`で始める。"
    "複数の策は`自動検出: <策>。文書: <策>`のように策ごとに語を置く"
    "（定義元は`agent-basics:bugfix`の`references/prevention.md`「`再発防止:`の行」）"
)


def starts_with_kind(text: str) -> bool:
    return _KIND_HEAD.match(text.lstrip(_LEADING_DECORATION)) is not None


TEXT_ONLY_KINDS = frozenset({"手順", "文書"})
"""判断時に思い出して適用することに頼る種類。再発した事象へこれだけを重ねると、同じ未適用を繰り返す。 pyfltr: allow-comment"""

STRUCTURE_EXCEPTION_LABEL = "構造化不可"
"""再発への対策が文書・手順だけになる時に、構造・検査で防げない理由を書く欄の語。 pyfltr: allow-comment"""

_ADOPTED_LABEL = re.compile(
    r"(?:^|[。．;；、,\s])(?P<kind>"
    + "|".join(re.escape(kind) for kind in sorted(ADOPTED_KINDS, key=len, reverse=True))
    + r")[ \t]*[:：]"
)


def adopted_kinds(text: str) -> set[str]:
    """`再発防止:`の行の本文から、採る策の種類の語を全て返す。

    行頭の語と、`<語>:`の形で並べた語を数える。 pyfltr: allow-comment
    """
    body = text.lstrip(_LEADING_DECORATION)
    found = {match["kind"] for match in _ADOPTED_LABEL.finditer(body)}
    head = _KIND_HEAD.match(body)
    if head is not None and head.group(0) in ADOPTED_KINDS:
        found.add(head.group(0))
    return found


RECURRENCE_KINDS = ("未適用", "範囲不足", "効果不足", "読込喪失", "削除", "未配布", "誘発入力")
"""`再発:`の行の先頭に置く、前回の対策が防げなかった理由の区分。

定義元は`agent-basics:bugfix`の`references/recurrence.md`「`再発:`の行」。 pyfltr: allow-comment
"""

RECURRENCE_NOT_APPLICABLE = "該当なし"
"""ユーザーの原文が反復に触れても、前回の対策が無かった時に`再発:`の行の先頭に置く語。 pyfltr: allow-comment"""

_RECURRENCE_HEAD = re.compile(
    "(?:"
    + "|".join(re.escape(kind) for kind in (*RECURRENCE_KINDS, RECURRENCE_NOT_APPLICABLE))
    + r")(?=$|[\s:：、。,（(*_`・])"
)

RECURRENCE_GUIDANCE = (
    "前回の対策が防げなかった理由を"
    + "・".join(f"`{kind}`" for kind in RECURRENCE_KINDS)
    + f"のどれかで始め、前回の対策が無い時は`{RECURRENCE_NOT_APPLICABLE}`で始めて理由を書く"
    "（定義元は`agent-basics:bugfix`の`references/recurrence.md`「`再発:`の行」）"
)


def recurrence_kind(text: str) -> str | None:
    """`再発:`の行の本文の先頭にある区分の語を返す。無ければNoneを返す。 pyfltr: allow-comment"""
    match = _RECURRENCE_HEAD.match(text.lstrip(_LEADING_DECORATION))
    return match.group(0) if match is not None else None


_RECURRENCE_MENTION = re.compile(
    r"何度も|何回も|何度か|前にも|以前にも|以前も|過去にも|過去に何|また同じ|毎回|繰り返し|再発(?!防止)"
)


def mentions_recurrence(text: str) -> bool:
    """ユーザーの原文が、同じ問題の反復に触れる語を含むかを返す。 pyfltr: allow-comment"""
    return _RECURRENCE_MENTION.search(text) is not None
