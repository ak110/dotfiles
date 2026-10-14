"""PreToolUse統合フックの責務別実装。"""

import sys

# 関数の実行時までだけ使う依存を、使用時まで読み込まない。
lazy from agent_toolkit._hooks import termination_evidence
lazy from agent_toolkit._hooks.pretooluse import dispatch as _dispatch


def main(payload_text: str) -> int:
    """元の操作の判定を保ち、呼び出しの開始位置を終了工程の供給先へ渡す。"""
    try:
        termination_evidence.observe_tool(payload_text, after=False)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の開始位置を取得できない: {error}", file=sys.stderr)
    return _dispatch.main(payload_text)


__all__ = ["main"]
