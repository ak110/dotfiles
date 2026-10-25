"""WI本文を保存する前に口語表現とダッシュの警告を集める。"""

import json
import re

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy from pyfltr.colloquial import check as colloquial

from agent_toolkit._atk import outcome

_DASH = re.compile("—|―|──")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.\-]*://|www\.)[^\s)]+")
_DETAIL_PREFIX = "本文:"
_SUMMARY_PREFIX = "WI本文の表記診断: "
_BODY_WARNING = re.compile(r"^警告: " + re.escape(_DETAIL_PREFIX) + r"\d+:\d+: (?:口語表現|ダッシュ) ")
_BODY_WARNING_CONTEXT = re.compile(
    r'^警告: WI本文の表記診断: 入力元="(?:[^"\\]|\\.)*" 論理本文:\d+:\d+: (?:口語表現|ダッシュ) '
)
_BODY_WARNING_SUMMARY = re.compile(r"^警告: " + re.escape(_SUMMARY_PREFIX) + r"[1-9]\d*件(?:\s|$)")
"""件数だけの要約行。現行版は書かないが、過去の記録を読むsession-reviewが同じ診断として識別する。"""


def is_body_style_diagnostic_warning(text: str) -> bool:
    """保存前に処置するWI本文の表記診断かを表示文言から判定する。"""
    return bool(_BODY_WARNING.match(text) or _BODY_WARNING_CONTEXT.match(text) or _BODY_WARNING_SUMMARY.match(text))


def warnings_for_body(body: str, *, source: str = "本文入力") -> list[str]:
    """フェンス内を除いて、入力元・論理本文の座標・一致箇所の文脈を返す。"""
    warnings: list[tuple[int, int, str]] = []
    lines = body.splitlines()

    def append_warning(line: int, column: int, matched: str, diagnostic: str) -> None:
        raw = lines[line - 1]
        start = column - 1
        end = start + len(matched)
        # 長い行も一致の両側を読める量に留め、同じ語の出現箇所を囲んで示す。
        left = max(0, start - 30)
        right = min(len(raw), end + 30)
        snippet = ("…" if left else "") + raw[left:start] + "【" + raw[start:end] + "】" + raw[end:right]
        if right < len(raw):
            snippet += "…"
        warning = (
            f"{_SUMMARY_PREFIX}入力元={json.dumps(source, ensure_ascii=False)} "
            f"論理本文:{line}:{column}: {diagnostic} | 文脈: {snippet}"
        )
        warnings.append((line, column, warning))

    # 漢語複合語の末尾（「将来いずれ」など）を口語表現として報告しないよう、
    # pyfltr自身の口語表現チェックと同じくdenylistへ漢字の左境界条件を付ける。
    deny = colloquial.load_patterns(colloquial.DENY_PATH, kanji_left_boundary=True)
    allow = colloquial.load_patterns(colloquial.ALLOW_PATH)
    for line, column, matched, _snippet, replacement in colloquial.scan_text(body, deny, allow):
        suggestion = f"（候補: {replacement}）" if replacement else ""
        append_warning(line, column, matched, f"口語表現 {matched}{suggestion}")

    masked = colloquial.mask_fenced_code_blocks(body)
    for line, raw in enumerate(masked.splitlines(), start=1):
        searchable = _INLINE_CODE.sub(lambda match: " " * len(match.group()), raw)
        searchable = _URL.sub(lambda match: " " * len(match.group()), searchable)
        for match in _DASH.finditer(searchable):
            append_warning(line, match.start() + 1, match.group(), f"ダッシュ {match.group()}")
    return [warning for _line, _column, warning in sorted(warnings, key=lambda item: item[:2])]


def report_warnings(warnings: list[str], *, next_action: str) -> None:
    """診断の全量を標準エラーへ直接書く。長い場合はエージェント環境の共通の保存機構が全量を保存する。"""
    if warnings:
        outcome.report_warning("\n警告: ".join(warnings), next_action=next_action)
