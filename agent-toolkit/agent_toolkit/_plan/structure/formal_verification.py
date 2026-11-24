from __future__ import annotations

import contextlib
import re

from agent_toolkit._common import markdown_headings
from agent_toolkit._plan.structure.constants import (
    PLAN_H2_CURRENT_PROGRESS,
    PLAN_H2_CURRENT_VERIFICATION,
    REQUEST_H2_QUOTATION,
)
from agent_toolkit._plan.structure.sections_history import progress_log_rows

JUDGMENT_LABEL = "形式検証の判定"

TRIGGER_TERMS: tuple[str, ...] = (
    "並行",
    "ロック",
    "排他",
    "再試行",
    "状態遷移",
    "永続データ",
    "構文解析",
    "認可",
    "停止性",
    "リファクタリング",
    "等価",
    "桁あふれ",
    "オーバーフロー",
    "ビット演算",
    "固定幅",
    "境界値",
    "性能改善",
    "高速化",
    "最適化",
)

_TERM_PATTERNS: dict[str, re.Pattern[str]] = {
    term: re.compile(re.escape(term) + ("(?!ファイル)" if term == "ロック" else "")) for term in TRIGGER_TERMS
}
_JUDGMENT_RE = re.compile(
    rf"(?:[-*+][ \t]+)?{JUDGMENT_LABEL}[:：][ \t]*(?:当てる|当てない)[ \t]*[（(][^）)\n]*[^\s）)][^）)\n]*[）)]"
)
_UNSCANNED_SECTIONS = frozenset({PLAN_H2_CURRENT_PROGRESS, REQUEST_H2_QUOTATION})


def _scanned_text(text: str) -> str:
    normalized = markdown_headings.normalize_newlines(text)
    sections = markdown_headings.h2_section_bodies(normalized)
    toplevel = markdown_headings.top_level_atx_headings(normalized, 2)
    first = toplevel[0][0].map[0] if toplevel and toplevel[0][0].map is not None else None
    head = normalized.split("\n")[:first] if first is not None else normalized.split("\n")
    parts = ["\n".join(head)]
    parts.extend("\n".join(body) for name, body in sections if name not in _UNSCANNED_SECTIONS)
    return "\n".join(parts)


def _recorded_lines(text: str) -> list[str]:
    normalized = markdown_headings.normalize_newlines(text)
    lines = [
        line
        for name, body in markdown_headings.h2_section_bodies(normalized)
        if name == PLAN_H2_CURRENT_VERIFICATION
        for line in markdown_headings.without_literal_blocks("\n".join(body)).split("\n")
    ]
    with contextlib.suppress(ValueError):
        lines.extend(result for _at, _step, result in progress_log_rows(normalized))
    return lines


def missing_judgment_message(text: str) -> str | None:
    if any(_JUDGMENT_RE.match(line.strip()) for line in _recorded_lines(text)):
        return None
    scanned = _scanned_text(text)
    found = [term for term, pattern in _TERM_PATTERNS.items() if pattern.search(scanned)]
    if not found:
        return None
    return (
        f"計画が形式検証の判定の対象に触れる語（{'、'.join(found)}）を含みますが、結論と理由を書いた`{JUDGMENT_LABEL}:`の行がありません。"
        "`agent-basics:formal-verification`「当てるかの判定」で判定し、"
        f"`## 検証`の表の後へ`{JUDGMENT_LABEL}: <当てる|当てない>（理由）`の1行を書いてください。"
        "実装に着手した後の計画では、`atk run-script plan-progress`の`--result`で同じ行を進捗ログへ追記してください"
    )
