"""会話記録へ実行環境が挿入した本文と構造標識を共通判定する。"""

from __future__ import annotations

from typing import Any

_RUNTIME_INSERTED_PREFIXES = (
    "<system-reminder>",
    "[COMPACTION RECOVERY]",
    "This session is being continued",
    "<normative-context",
    "<agent-toolkit-auto-inserted",
    "<agent-toolkit-hook-message",
    "<task-notification>",
    "<command-name>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "A session-scoped Stop hook is now active",
    "Goal check-in:",
    "Stop hook feedback:",
    "# AGENTS.md instructions",
    "<environment_context>",
    "Base directory for this skill:",
    "<multi_agent_mode>",
    "<multi_agent_role>",
    "You are `/root`, the primary agent in a team of agents collaborating to fulfill the user's goals.",
    "<skills_instructions>",
    "<permissions instructions>",
    "<recommended_plugins>",
    "<collaboration_mode>",
    "<plugins_instructions>",
    "<apps_instructions>",
    "<cross-session-message",
    "<model_switch>",
    "<turn_aborted>",
    "Another Claude session sent a message:",
)


def is_runtime_inserted_text(text: str) -> bool:
    """本文の先頭が実行環境、hookまたは委譲の配送に当たる場合に真を返す。"""
    stripped = text.lstrip()
    if stripped.startswith("<skill>") and "</skill>" in stripped:
        return True
    return stripped.startswith(_RUNTIME_INSERTED_PREFIXES)


def is_runtime_generated(entry: dict[str, Any]) -> bool:
    """Claude Codeが付けた挿入本文の構造標識を判定する。"""
    return entry.get("isMeta") is True or entry.get("turnCompanion") is True
