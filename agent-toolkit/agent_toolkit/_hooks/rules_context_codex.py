"""Codex向け射影からClaude Code固有の条文を除いて追加するhandler。"""

from __future__ import annotations

# 関数の実行時までだけ使う依存を、Python 3.15では使用時まで読み込まない。旧Pythonでは通常のimportとなる。
__lazy_modules__ = {"agent_toolkit._hooks"}

from agent_toolkit._hooks import rules_context


def main(payload_text: str) -> int:
    """Codex向けのSessionStart payloadを処理する。"""
    return rules_context.main(payload_text, host="codex")
