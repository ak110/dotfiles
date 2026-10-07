"""PreToolUse統合フックの責務別実装。"""

import sys

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._hooks.pretooluse import dispatch as _dispatch


def main(payload_text: str) -> int:
    """元の操作の判定を保ち、呼び出しの開始位置を終了工程の供給先へ渡す。"""
    try:
        termination_evidence.observe_tool(payload_text, after=False)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の開始位置を取得できない: {error}", file=sys.stderr)
    return _dispatch.main(payload_text)


__all__ = ["main"]
