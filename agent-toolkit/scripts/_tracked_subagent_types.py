"""Claude Code agent-toolkit: `_process_loop_log`記録対象のサブエージェント種別定数。

`pretooluse.py`（起動時刻記録）と`posttooluse.py`（終了時刻記録）の両方が
同一の対象種別集合を参照するため、SSOTとして本モジュールへ集約する。
フルネームと短縮名の両方を許容する。

`SUBAGENT_TYPE_FLAGS`はレビュー担当エージェント種別→セッション状態フラグ名のマップであり、
`pretooluse.py`（起動要求検知時点での即時記録）と`posttooluse.py`（完了時点での記録）の
両方が参照する。
"""

from __future__ import annotations

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
