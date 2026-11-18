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
_REPORT_LINE = re.compile(r"^ {0,3}(?:(?:[-*+]|\d+[.)])[ \t]+)?(.*?)\s*$")
_LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d+[.)])[ \t]")
_INLINE_CODE = re.compile(r"`[^`\n]+`")

CUE_NEGATION = (
    r"(?!(?:は|が|も|では)?(?:無|な)(?:い|く|し|かっ)|(?:は|が|も)?見つから"
    r"|(?:し|て|して)?い?(?:な(?:い|く|かっ)|ませ)|せず|(?:は|が)?[0０]件)"
)
"""判定語の直後が否定（「誤りは無い」「失敗しない」「漏れなく」「0件」など）の場合を除く先読み。"""
REPORT_CUES = {
    "deviation": (
        r"(?:バグ|不具合|(?<!非)欠陥|不備|不整合|誤り|誤っ|間違(?:い|え|っ)|ミス|漏れ|見落と|取り違|失念"
        r"|忘れ(?:てい|た)|抜け(?:てい|落ち)|逸脱|退行|壊れ|壊し|失敗(?!例|時|した場合|する場合)"
        r"|エラー(?:が(?:出|発生|起き|残)|にな)|指摘|再発(?!防止))"
    ),
    "prevention": (
        r"(?:ユーザー(?:から|に|が)?の?再?指摘|再指摘|再発(?!防止)|同じ(?:誤り|失敗|ミス|不具合|症状|問題|害|指摘)"
        r"|(?:2|２|二)(?:回|度)目の(?:修正|失敗|指摘)|破損|消失|漏洩|漏えい|依頼の未達)"
    ),
    "absence": (
        r"(?:仕組み|規範|規定|手順|検査|機構|経路|契約|試験|テスト|条文|定め|ルール|対策|hook|フック)"
        r"(?:(?:が|は|も)(?:無|な)(?:い|く|かっ)|(?:が|は)?存在しな|(?:が|は)?欠けてい|の不在)"
    ),
    "no_change": r"(?:変えて|変更して|触って|いじって|編集して|書き換えて|手を加えて|改変して)い?(?:ない|ません|おらず)",
}
"""報告の形の手掛かりと判定語。語の一致で案内の候補を選ぶだけで、意味は推定しない。

`deviation`は逸脱を示す報告、`prevention`は再発防止を検討する条件（ユーザーの指摘、同じ害の繰り返し、大きな害）、
`absence`は仕組みや規範の不在の主張、`no_change`は変更の否定で、`agent-basics:bugfix`の入口と確認観点の条件に対応する。
`hooks/send_to_user.tsx`は送達後の検査に使う種類を同じ文字列で持つ。判定語を変える場合は両方を合わせる。
"""
_CUE_PATTERNS = {cue: re.compile(terms + CUE_NEGATION) for cue, terms in REPORT_CUES.items()}


def report_line(line: str) -> str | None:
    """1行が報告の本文なら項目記号と字下げを除いた本文を返し、引用・コード例の行は報告として扱わない。"""
    if line.startswith("\t") or line.lstrip().startswith(">"):
        return None
    if not _LIST_ITEM.match(line) and line.startswith("    "):
        return None
    matched = _REPORT_LINE.match(line)
    return matched[1] if matched else None


def improvement_line(line: str) -> str | None:
    """1行が改善点の報告行なら項目記号と字下げを除いた本文を返し、引用・コード例の行は報告として扱わない。"""
    body = report_line(line)
    return body if body is not None and body.startswith(IMPROVEMENT_MARKER) else None


def report_lines(text: str) -> list[str]:
    """自動挿入の要素、フェンス内、引用、字下げのコード例を除いた報告の本文行を、項目記号を外して返す。"""
    text = re.sub(rf"<({_message_format.AUTO_ELEMENT_NAME_PATTERN})(?:\s[^>]*)?>.*?</\1>", "", text, flags=re.DOTALL)
    found: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = re.match(r"(`{3,}|~{3,})", line.lstrip())
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        body = report_line(line) if fence is None else None
        if body is not None:
            found.append(body)
    return found


def reported_improvement_lines(text: str, *, deduplicate: bool = True) -> list[str]:
    """実際に報告した行を例から区別し、出所の行順が必要な照会では同文行も保持する。"""
    found = [line for line in report_lines(text) if line.startswith(IMPROVEMENT_MARKER)]
    return list(dict.fromkeys(found)) if deduplicate else found


def report_cues(text: str) -> frozenset[str]:
    """報告の本文行（インラインコードを除く）が含む`REPORT_CUES`の手掛かりを返す。"""
    body = "\n".join(_INLINE_CODE.sub("", line) for line in report_lines(text))
    return frozenset(cue for cue, pattern in _CUE_PATTERNS.items() if pattern.search(body))
