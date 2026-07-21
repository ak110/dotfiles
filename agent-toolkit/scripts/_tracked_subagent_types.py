"""Claude Code agent-toolkit: `_process_loop_log`記録対象のサブエージェント種別定数。

`pretooluse.py`（起動時刻記録）と`posttooluse.py`（終了時刻記録）の両方が
同一の対象種別集合を参照するため、SSOTとして本モジュールへ集約する。
フルネームと短縮名の両方を許容する。
"""

from __future__ import annotations

TRACKED_SUBAGENT_TYPES: frozenset[str] = frozenset(
    {
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
        "plan-implementer",
        "agent-toolkit:plan-implementer",
        "plan-codex-implementer",
        "agent-toolkit:plan-codex-implementer",
        "plan-impl-reviewer",
        "agent-toolkit:plan-impl-reviewer",
        "plan-codex-reviewer",
        "agent-toolkit:plan-codex-reviewer",
        "plan-reviewer",
        "agent-toolkit:plan-reviewer",
        "plan-spec-reviewer",
        "agent-toolkit:plan-spec-reviewer",
        "agent-doc-validator",
        "agent-toolkit:agent-doc-validator",
        "plan-file-creator",
        "agent-toolkit:plan-file-creator",
    }
)
