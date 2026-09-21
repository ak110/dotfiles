"""PreToolUseの警告一覧を一つのadditionalContextへ整形する。"""

from __future__ import annotations

import re
from collections.abc import Sequence

_COUNT_HEADER_RE = re.compile(r"\A警告: \d+件\n\n")
_NOTICE_PREFIX_RE = re.compile(r'(?m)^<agent-toolkit-hook-message\b[^>]*\bkind="warn"[^>]*>')


def format_warning_context(warnings: Sequence[str]) -> str:
    """警告本文を結合し、複数件では総数を先頭へ付ける。"""
    if not warnings:
        return ""
    bodies = [_COUNT_HEADER_RE.sub("", warning, count=1) for warning in warnings]
    body = "\n\n".join(bodies)
    count = sum(max(1, len(_NOTICE_PREFIX_RE.findall(warning))) for warning in bodies)
    if count == 1:
        return body
    return f"警告: {count}件\n\n{body}"
