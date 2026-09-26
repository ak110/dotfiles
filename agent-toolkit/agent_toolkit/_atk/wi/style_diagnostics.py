"""WI本文を保存する前に口語表現とダッシュの警告を集める。"""

import re

from pyfltr.colloquial import check as colloquial

_DASH = re.compile("—|―|──")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.\-]*://|www\.)[^\s)]+")


def warnings_for_body(body: str) -> list[str]:
    """フェンス内を除いて、本文の行番号付き警告を返す。"""
    warnings: list[str] = []
    # 漢語複合語の末尾（「将来いずれ」など）を口語表現として報告しないよう、
    # pyfltr自身の口語表現チェックと同じくdenylistへ漢字の左境界条件を付ける。
    deny = colloquial.load_patterns(colloquial.DENY_PATH, kanji_left_boundary=True)
    allow = colloquial.load_patterns(colloquial.ALLOW_PATH)
    for line, column, matched, _snippet, replacement in colloquial.scan_text(body, deny, allow):
        suggestion = f"（候補: {replacement}）" if replacement else ""
        warnings.append(f"本文:{line}:{column}: 口語表現 {matched}{suggestion}")

    masked = colloquial.mask_fenced_code_blocks(body)
    for line, raw in enumerate(masked.splitlines(), start=1):
        searchable = _INLINE_CODE.sub(lambda match: " " * len(match.group()), raw)
        searchable = _URL.sub(lambda match: " " * len(match.group()), searchable)
        for match in _DASH.finditer(searchable):
            warnings.append(f"本文:{line}:{match.start() + 1}: ダッシュ {match.group()}")
    return sorted(warnings, key=lambda warning: tuple(int(value) for value in warning.split(":")[1:3]))
