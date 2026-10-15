"""Codex向け射影からClaude Code固有の条文を除いて追加するhandler。"""

from __future__ import annotations

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._hooks import rules_context


def main(payload_text: str) -> int:
    """Codex向けのSessionStart payloadを処理する。"""
    return rules_context.main(payload_text, host="codex")
