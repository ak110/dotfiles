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
