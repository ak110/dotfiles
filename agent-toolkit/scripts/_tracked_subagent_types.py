"""Claude Code agent-toolkit: `_process_loop_log`記録対象のサブエージェント種別定数。

`pretooluse.py`（起動時刻記録）と`posttooluse.py`（終了時刻記録）の両方が
同一の対象種別集合を参照するため、SSOTとして本モジュールへ集約する。
フルネームと短縮名の両方を許容する。

`SUBAGENT_TYPE_FLAGS`はレビュー担当エージェント種別→セッション状態フラグ名のマップであり、
`pretooluse.py`（起動要求検知時点での即時記録）と`posttooluse.py`（完了時点での記録）の
両方が参照する。

`is_review_purpose`は`plan-codex-delegate`の起動プロンプトが指定する用途を判定する。
同エージェントは計画作成・計画レビュー・実装差分レビュー・実装の4用途を持ち、
用途が計画作成・実装の起動でレビュー実施済みフラグを記録すると、レビュー未実施のまま
計画作成工程の完遂判定を通過できてしまうため、記録をレビュー2用途へ限定する目的で使う。

`is_explicit_review_purpose`は用途がレビュー2用途と明示されている場合だけ真を返す。
用途行が無い場合に真を返す`is_review_purpose`は記録側へ倒す判定であり、
レビュー用途の編集遮断へ流用すると用途行を持たない正当な編集までブロックするため、
遮断側の判定として本関数を分けて設ける。
"""

from __future__ import annotations

import re

TRACKED_SUBAGENT_TYPES: frozenset[str] = frozenset(
    {
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
        "plan-implementer",
        "agent-toolkit:plan-implementer",
        "plan-codex-delegate",
        "agent-toolkit:plan-codex-delegate",
        "plan-reviewer",
        "agent-toolkit:plan-reviewer",
        "plan-file-creator",
        "agent-toolkit:plan-file-creator",
    }
)

SUBAGENT_TYPE_FLAGS: dict[str, str] = {
    "plan-reviewer": "plan_reviewer_invoked",
    "agent-toolkit:plan-reviewer": "plan_reviewer_invoked",
    "plan-codex-delegate": "codex_review_invoked",
    "agent-toolkit:plan-codex-delegate": "codex_review_invoked",
}

_PURPOSE_RE = re.compile(r"用途\s*[:：]\s*(\S+)")

# 成果物編集の遮断対象とする用途。`plan-codex-delegate.md`が定める4用途のうちレビュー2用途に限る。
EXPLICIT_REVIEW_PURPOSES: frozenset[str] = frozenset({"計画レビュー", "実装差分レビュー"})


def is_review_purpose(prompt: str) -> bool:
    """起動プロンプトがレビュー用途を指定しているかを返す。

    用途の記述が見つからない場合は真を返す。
    レビュー起動を実装起動と誤判定するとレビュー実施済みの記録が漏れ、
    実装工程の事前チェックが正当な進行をブロックするため、判定不能時は記録側へ倒す。
    """
    match = _PURPOSE_RE.search(prompt)
    if match is None:
        return True
    return "レビュー" in match.group(1)


def is_explicit_review_purpose(purpose: object) -> bool:
    """用途がレビュー2用途（計画レビュー・実装差分レビュー）と明示されているかを返す。

    `用途: <値>`形式の行と値単体の双方を受け付け、値が`EXPLICIT_REVIEW_PURPOSES`と
    完全一致する場合のみ真を返す。用途の記述が無い場合、値が計画作成・実装の場合、
    文字列以外を受け取った場合はいずれも偽を返す。
    レビュー用途の編集遮断が判定材料を観測できない状況で正当な編集を止めないための安全側の契約とする。
    """
    if not isinstance(purpose, str) or not purpose:
        return False
    match = _PURPOSE_RE.search(purpose)
    value = match.group(1) if match else purpose.strip()
    return value in EXPLICIT_REVIEW_PURPOSES
