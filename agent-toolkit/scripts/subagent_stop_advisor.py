"""SubagentStop hook: 空の完了報告を検査する。

公式仕様の`last_assistant_message`を直参照し、空文字列だけの完了報告をblockする。
成果物と検証結果の妥当性は呼び出し元の実測と実装レビュー担当のレビューへ委ねる。

正常許可と`stop_hook_active`真の再呼び出し時は、両ホスト共通でstdoutを空にする。
transcriptを完了判定の契約へ利用せず、安定入力の`last_assistant_message`による空報告検査だけを共有する。

委譲先での実行可否: 委譲先も空でない完了報告を再出力できるため、除外せず遮断できる。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

# pylint: disable-next=wrong-import-position,import-error
from _hook_notice import block_formatter as _block_notice_formatter  # noqa: E402

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

    if _is_empty_completion_report(payload.get("last_assistant_message")):
        reason = _block_notice(
            "停止する前に、空でない完了報告を出力する。呼び出し元は遮断された報告本文を保持しない。",
            fix="空でない完了報告を書いてから、あらためて停止する。",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    return 0
