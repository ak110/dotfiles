"""Codex向け射影からClaude Code固有の条文を除いて追加するhandler。"""

from __future__ import annotations

from agent_toolkit._hooks import rules_context


def main(payload_text: str) -> int:
    """Codex向けのSessionStart payloadを処理する。"""
    return rules_context.main(payload_text, host="codex")
