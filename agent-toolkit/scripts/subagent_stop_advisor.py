"""SubagentStop hook: 構造的に確定できる未完了状態と報告言語を検査する。

公式仕様の`last_assistant_message`を直参照し、空文字列だけの完了報告をblockする。
報告本文のラベルや意味は解析せず、記述言語だけを機械判定する。
成果物と検証結果の妥当性は呼び出し元の実測と実装レビュー担当のレビューへ委ねる。

正常許可と`stop_hook_active`真の再呼び出し時は、両ホスト共通でstdoutを空にする。
transcriptを完了判定の契約へ利用せず、安定入力の`last_assistant_message`による空報告検査だけを共有する。
言語ゲートはClaude Code専用とし、Codexでは`reason`の配送先と再提出の成立を確認できないため実行しない。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import _hook_tool_input  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# pylint: disable-next=wrong-import-position,import-error
from _hook_notice import block_formatter as _block_notice_formatter  # noqa: E402
from _response_language_check import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    SUBAGENT_REPORT_BLOCK_BODY,
    CheckOutcome,
    check_text,
)

_HOOK_ID = "agent-toolkit/subagent-stop"


def _is_empty_completion_report(text: object) -> bool:
    """完了報告が空文字列だけで構成される場合に真を返す。"""
    return isinstance(text, str) and not text.strip()


_block_notice = _block_notice_formatter(_HOOK_ID)


def main(payload_text: str) -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0

    if payload.get("stop_hook_active") is True:
        return 0

    is_codex = _hook_tool_input.is_codex_payload(payload)
    if _is_empty_completion_report(payload.get("last_assistant_message")):
        reason = _block_notice(
            "Provide a non-empty completion report before stopping. The caller does not retain a blocked report body.",
            fix="Write a non-empty completion report and stop again.",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    report = payload.get("last_assistant_message")
    if not is_codex and isinstance(report, str):
        outcome, _ = check_text(report)
        if outcome is CheckOutcome.WARN:
            reason = _block_notice(
                SUBAGENT_REPORT_BLOCK_BODY,
                fix="Rewrite the completion report in Japanese and resubmit the full report.",
            )
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
            return 0

    return 0
