"""WI本文を保存する前に口語表現とダッシュの警告を集める。"""

import re

from pyfltr.colloquial import check as colloquial

from agent_toolkit._atk import managed_temp, outcome, output_file
from agent_toolkit._atk.environment import is_agent_environment

_DASH = re.compile("—|―|──")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.\-]*://|www\.)[^\s)]+")
_DETAIL_PREFIX = "本文:"
_SUMMARY_PREFIX = "WI本文の表記診断: "
_BODY_WARNING = re.compile(r"^警告: " + re.escape(_DETAIL_PREFIX) + r"\d+:\d+: (?:口語表現|ダッシュ) ")
_BODY_WARNING_SUMMARY = re.compile(r"^警告: " + re.escape(_SUMMARY_PREFIX) + r"[1-9]\d*件(?:\s|$)")


def is_body_style_diagnostic_warning(text: str) -> bool:
    """保存前に処置するWI本文の表記診断かを表示文言から判定する。"""
    return bool(_BODY_WARNING.match(text) or _BODY_WARNING_SUMMARY.match(text))


def warnings_for_body(body: str) -> list[str]:
    """フェンス内を除いて、本文の行番号付き警告を返す。"""
    warnings: list[str] = []
    # 漢語複合語の末尾（「将来いずれ」など）を口語表現として報告しないよう、
    # pyfltr自身の口語表現チェックと同じくdenylistへ漢字の左境界条件を付ける。
    deny = colloquial.load_patterns(colloquial.DENY_PATH, kanji_left_boundary=True)
    allow = colloquial.load_patterns(colloquial.ALLOW_PATH)
    for line, column, matched, _snippet, replacement in colloquial.scan_text(body, deny, allow):
        suggestion = f"（候補: {replacement}）" if replacement else ""
        warnings.append(f"{_DETAIL_PREFIX}{line}:{column}: 口語表現 {matched}{suggestion}")

    masked = colloquial.mask_fenced_code_blocks(body)
    for line, raw in enumerate(masked.splitlines(), start=1):
        searchable = _INLINE_CODE.sub(lambda match: " " * len(match.group()), raw)
        searchable = _URL.sub(lambda match: " " * len(match.group()), searchable)
        for match in _DASH.finditer(searchable):
            warnings.append(f"{_DETAIL_PREFIX}{line}:{match.start() + 1}: ダッシュ {match.group()}")
    return sorted(warnings, key=lambda warning: tuple(int(value) for value in warning.split(":")[1:3]))


def report_warnings(warnings: list[str], *, next_action: str) -> None:
    """エージェントには診断の全量を保存し、件数・保存先・続行可否を返す。"""
    if not warnings:
        return
    details = "\n警告: ".join(warnings)
    if is_agent_environment():
        try:
            path = output_file.save_text(
                details + "\n", lambda: managed_temp.create_managed_temp("atk-diagnostics"), filename="stderr.txt"
            )
        except Exception as error:  # noqa: BLE001  # 診断保存の失敗でも本来の詳細と結果を保持する
            outcome.report_warning(
                f"表記診断を保存できないため全量を表示する: {error}\n警告: {details}", next_action=next_action
            )
        else:
            outcome.report_warning(f"{_SUMMARY_PREFIX}{len(warnings)}件\n標準エラー詳細保存先: {path}", next_action=next_action)
    else:
        outcome.report_warning(details, next_action=next_action)
