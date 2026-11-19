"""Codex向け射影から、Claude Code固有の値を除いて入口の起動の案内を追加するhandler。"""

from __future__ import annotations

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._hooks import rules_context


def main(payload_text: str) -> int:
    """Codex向けのSessionStart payloadを処理する。"""
    return rules_context.main(payload_text, host="codex")
