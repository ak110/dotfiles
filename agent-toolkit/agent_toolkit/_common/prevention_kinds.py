"""`再発防止:`の行の先頭に置く対策の種類の語と、その判定。  pyfltr: allow-comment

語の定義元は`agent-basics:bugfix`の`references/prevention.md`「`再発防止:`の行」である。
AWIの原因分析の保存時の検査（`_plan/structure/sections_bugs.py`）で使う。
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


RECURRENCE_KINDS = ("未適用", "範囲不足", "効果不足", "読込喪失", "削除", "未配布", "誘発入力")
"""`再発:`の行の先頭に置く、前回の対策が防げなかった理由の区分。

定義元は`agent-basics:bugfix`の`references/recurrence.md`「`再発:`の行」。 pyfltr: allow-comment
"""

RECURRENCE_NOT_APPLICABLE = "該当なし"
"""前回の対策が無かった時と、逐語引用が同じ問題の反復を述べていなかった時に`再発:`の行の先頭に置く語。 pyfltr: allow-comment"""

_RECURRENCE_HEAD = re.compile(
    "(?:"
    + "|".join(re.escape(kind) for kind in (*RECURRENCE_KINDS, RECURRENCE_NOT_APPLICABLE))
    + r")(?=$|[\s:：、。,（(*_`・])"
)

RECURRENCE_GUIDANCE = (
    "前回の対策が防げなかった理由を"
    + "・".join(f"`{kind}`" for kind in RECURRENCE_KINDS)
    + "のどれかで始め、前回の対策が無い時と、引用が同じ問題の反復を述べていない時は"
    + f"`{RECURRENCE_NOT_APPLICABLE}`で始めて根拠を書く"
    "（定義元は`agent-basics:bugfix`の`references/recurrence.md`「`再発:`の行」）"
)


def recurrence_reason(text: str) -> str:
    """`再発:`の行の本文から、区分の語と区切りを除いた理由を返す。区分が無ければ空文字列を返す。 pyfltr: allow-comment"""
    body = text.lstrip(_LEADING_DECORATION)
    match = _RECURRENCE_HEAD.match(body)
    if match is None:
        return ""
    return body[match.end() :].strip(" \t　:：、。,・;；.").strip()


def recurrence_kind(text: str) -> str | None:
    """`再発:`の行の本文の先頭にある区分の語を返す。無ければNoneを返す。 pyfltr: allow-comment"""
    match = _RECURRENCE_HEAD.match(text.lstrip(_LEADING_DECORATION))
    return match.group(0) if match is not None else None


_RECURRENCE_MENTION = re.compile(
    r"(?:何度も|何回も|何度か|前にも|以前にも|以前も|過去にも|過去に|前に|以前|繰り返し|毎日|毎回)[^。！？\n]{0,12}?"
    r"(?:指摘|言(?:って|った|って)|書(?:いて|いた)|伝え(?:て|た))"
    r"|(?:指摘|言った|伝えた)[^。！？\n]{0,12}?(?:再発|また同じ)"
)
"""ユーザーが同じ問題を繰り返し指摘したと読める言及。

語と距離による候補の検出であり、反復かどうかの確定ではない。
確定は起草者が`再発:`の行で示し、反復でなければ`該当なし`と根拠の1行で保存できる。
候補は、本人が過去に指摘・発言したことと、その反復か再発を同じ文で述べた時に限る。
単語だけの判定は「何度も表示される」「以前も使った」「何度も入力を間違える研修」などの定常の挙動や、
意図した反復まで止めたため（2026年11月25日の実行レビュー）、不具合の反復と確定できない文では止めない。 pyfltr: allow-comment
"""


def mentions_recurrence(text: str) -> bool:
    """ユーザーの原文が、過去の指摘の反復か再発を述べているかを返す。 pyfltr: allow-comment"""
    return _RECURRENCE_MENTION.search(text) is not None
