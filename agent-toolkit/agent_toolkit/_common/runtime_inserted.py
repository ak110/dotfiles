"""セッション記録へ実行環境が挿入した本文と構造標識を共通判定する。"""

from __future__ import annotations

import re
from typing import Any

from agent_toolkit._common import message_format as _message_format

IMPROVEMENT_MARKER = "気付いた改善点:"
"""メインと委譲先が改善機会を報告する行の先頭。長い本文の短縮でも実際の行を保持する。"""

_RUNTIME_INSERTED_PREFIXES = (
    "You are Codex, an agent based on GPT-6.",
    "<system-reminder>",
    "[COMPACTION RECOVERY]",
    "This session is being continued",
    "<normative-context",
    *(f"<{element}" for element in _message_format.AUTO_ELEMENTS),
    "<task-notification>",
    "<command-name>",
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<bash-stdout>",
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
    "<codex_internal_context",
    "<hook_prompt",
    "<subagent_notification>",
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
    origin = entry.get("origin")
    return (
        entry.get("isMeta") is True
        or entry.get("turnCompanion") is True
        or isinstance(origin, dict)
        and origin.get("kind") == "plugin"
    )


# 委譲先は返却契約の行頭形式に加え、Markdownの箇条書きの項目記号を付けて報告することがある。
# 行頭0〜3スペースの後の`- `・`* `・`+ `・`1. `・`1) `を項目記号とし、4スペース以上とタブはコード例として除く。
_IMPROVEMENT_LINE = re.compile(rf"^ {{0,3}}(?:(?:[-*+]|\d+[.)])[ \t]+)?({re.escape(IMPROVEMENT_MARKER)}.*)$")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])[ \t]")


def improvement_line(line: str) -> str | None:
    """1行が改善点の報告行なら項目記号と字下げを除いた本文を返し、引用・コード例の行は報告として扱わない。"""
    if line.startswith("\t") or line.lstrip().startswith(">"):
        return None
    if not _LIST_ITEM.match(line) and line.startswith("    "):
        return None
    matched = _IMPROVEMENT_LINE.match(line)
    return matched[1].rstrip() if matched else None


def reported_improvement_lines(text: str, *, deduplicate: bool = True) -> list[str]:
    """実際に報告した行を例から区別し、出所の行順が必要な照会では同文行も保持する。"""
    text = re.sub(rf"<({_message_format.AUTO_ELEMENT_NAME_PATTERN})(?:\s[^>]*)?>.*?</\1>", "", text, flags=re.DOTALL)
    found: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        reported = improvement_line(line) if fence is None else None
        if reported is not None:
            found.append(reported)
    return list(dict.fromkeys(found)) if deduplicate else found
