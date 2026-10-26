"""Claude CodeとCodexの完了本文を共通の言語判定へ渡す。

委譲先での実行可否: 実行する。サブエージェントはSubagentStopが子の最終本文を渡す。

文字の比率と一致は説明言語の推定である。引用・素材の誤検出では、
完了本文をそのまま再出力して停止する通知を返す。
Codexは最後の応答を完了本文にするため、成果・判断・未達を含む全文を再出力する。
"""

import json

from agent_toolkit._common import response_language_check
from agent_toolkit._common.response_language_check import CheckOutcome, check_text
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import block_formatter

_block_notice = block_formatter("response-language")


def evaluate(payload_text: str) -> tuple[str, str]:
    """本文を取得できる初回停止だけを判定し、再帰的な遮断を避ける。"""
    try:
        payload = json.loads(payload_text)
    except ValueError, TypeError:
        return "", ""
    if not isinstance(payload, dict) or payload.get("stop_hook_active") is True:
        return "", ""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        return "", ""
    outcome, body = check_text(text)
    if outcome is not CheckOutcome.WARN:
        return "", ""
    recovery = (
        "基準を満たさない場合は成果・判断・未達と指定の返却形式を含む完了本文を変更せず、そのまま再出力して停止せよ。"
        if is_codex_payload(payload) or not is_main_agent_context(payload)
        else "基準を満たさない場合は`…`だけを出力して停止せよ。"
    )
    return "block", _block_notice(
        response_language_check.BLOCK_BODY
        + "判定基準: 検出した箇所が引用・入力素材・技術的な識別子か、エージェント自身の日本語以外の説明かを確かめよ。"
        f"検出した本文: {response_language_check._plain_text(text)[:200]}\n" + (body or "完了本文の説明を日本語で書け。"),  # pylint: disable=protected-access
        fix="基準を満たす場合は成果・判断・未達と指定の返却形式、引用・入力素材を保ち、説明を日本語で書いてから停止せよ。"
        + recovery
        + "再呼び出しは`stop_hook_active`で通過する。",
    )
