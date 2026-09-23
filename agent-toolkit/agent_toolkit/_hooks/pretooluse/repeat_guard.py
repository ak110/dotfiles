"""同一ツール呼び出しの連続反復を遮断する。

同一入力の10回連続実行は作業が進まない状態を示すため、処理停止を避ける遮断として維持する。
"""

from __future__ import annotations

import hashlib
import json
import sys

from agent_toolkit._hooks.notice import block_formatter
from agent_toolkit._hooks.session_state import update_state

_FINGERPRINT_KEY = "pretool_last_call_fingerprint"
_COUNT_KEY = "pretool_last_call_count"
_BLOCK_AT = 10
_block_notice = block_formatter("agent-toolkit/pretooluse/repeat-guard")


def _canonical_call(tool_name: str, tool_input: dict) -> str | None:
    """ツール名と入力を安定したJSONへ正規化する。"""
    try:
        return json.dumps(
            {"tool_name": tool_name, "tool_input": tool_input},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None


def check_repeated_tool_call(session_id: str, tool_name: str, tool_input: dict) -> bool:
    """同一呼び出しの10回目以降を遮断し、異なる呼び出しで連続数をリセットする。"""
    if not session_id or not isinstance(tool_name, str) or not tool_name:
        return False
    canonical = _canonical_call(tool_name, tool_input)
    if canonical is None:
        return False
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    count = 1

    def _record(current: dict) -> dict:
        nonlocal count
        previous_count = current.get(_COUNT_KEY)
        if current.get(_FINGERPRINT_KEY) == fingerprint and isinstance(previous_count, int) and previous_count >= 1:
            count = min(previous_count + 1, _BLOCK_AT)
        current[_FINGERPRINT_KEY] = fingerprint
        current[_COUNT_KEY] = count
        return current

    if not update_state(session_id, _record):
        return False
    if count < _BLOCK_AT:
        return False
    print(
        _block_notice(
            f"blocked: {tool_name}の同一呼び出しが{_BLOCK_AT}回連続した。",
            fix="同じ結果を再取得せず、直前の結果を使うか、状態を進める異なる操作を実行する。",
        ),
        file=sys.stderr,
    )
    return True
