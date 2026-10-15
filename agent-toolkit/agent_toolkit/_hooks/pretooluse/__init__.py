"""PreToolUse統合フックの責務別実装。"""

import sys

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._hooks import termination_evidence
lazy from agent_toolkit._hooks.pretooluse import dispatch as _dispatch


def main(payload_text: str) -> int:
    """ツール呼び出しの開始を終了工程の証拠へ記録してから、元の判定（`dispatch.main`）をそのまま実行する。"""
    try:
        termination_evidence.observe_tool(payload_text, after=False)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"終了工程の開始位置を取得できない: {error}", file=sys.stderr)
    return _dispatch.main(payload_text)


__all__ = ["main"]
