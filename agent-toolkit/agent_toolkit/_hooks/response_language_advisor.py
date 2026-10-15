"""Codexの完了本文を共通の言語判定へ渡す。"""

import json

from agent_toolkit._common.response_language_check import CheckOutcome, check_text
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import block_formatter

_block_notice = block_formatter("response-language")


def evaluate(payload_text: str) -> tuple[str, str]:
    """本文を取得できる初回停止だけを判定し、再帰的な遮断を避ける。"""
    try:
        payload = json.loads(payload_text)
    except ValueError, TypeError:
        return "", ""
    if not is_codex_payload(payload) or payload.get("stop_hook_active") is True:
        return "", ""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return "", ""
    outcome, body = check_text(text)
    if outcome is not CheckOutcome.WARN:
        return "", ""
    return "block", _block_notice(
        body or "完了本文の説明を日本語で書く。",
        fix="成果・判断・未達と指定の返却形式、引用・入力素材を保ち、説明を日本語で書いてから停止する。",
    )
