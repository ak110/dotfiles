#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""SubagentStop hook: 完了報告の本文を空/Skill単独報告と縮退表明・待機表明辞書で検査する。

公式仕様の`last_assistant_message`を直参照し、
`transcript_path`とisSidechain判定に依存しない。
`is_empty_completion_report`で実質空またはSkill呼び出し単独の構造的欠落を検出し、
続いて`_STOP_FOCUS_CATEGORIES_EXTENDED`と同一SSOTで縮退表明フレーズを照合する。
`stop_hook_active`真の再呼び出し時は判定処理をせず無条件approveを返し、
連続ブロック上限による強制終了を回避する。
"""

from __future__ import annotations

import json
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _message_format import llm_notice as _llm_notice_base  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _scope_escalation import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _STOP_FOCUS_CATEGORIES_EXTENDED,
    _match_scope_escalation,
    is_empty_completion_report,
)

_HOOK_ID = "agent-toolkit/subagent-stop"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM宛て通知メッセージを標準プレフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def main() -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    # Stop/SubagentStopフックの再帰呼び出し対策:
    # `stop_hook_active`真は直前の本hook呼び出しがブロックした再呼び出しを示す。
    # 連続ブロック上限到達による強制終了を避けるため、判定処理をせず無条件approveを返す。
    if payload.get("stop_hook_active") is True:
        print(json.dumps({"decision": "approve"}, ensure_ascii=False))
        return 0

    text = payload.get("last_assistant_message")
    if is_empty_completion_report(text):
        reason = _llm_notice(
            "blocked: サブエージェント完了報告が実質空、または`Skill`呼び出し単独。"
            "再委譲するか完遂本文を追記すること。"
            "再送する場合は訂正内容だけでなく、当初の完了報告本文全体を再掲したうえで追記・訂正すること"
            "（本フックのブロックでメイン側は本文を保持していないため）。",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    # `is_empty_completion_report`が非文字列・実質空を既に捕捉するため、
    # ここではtypeガードのみを残す。
    if not isinstance(text, str):
        return 0

    category = _match_scope_escalation(text, categories=_STOP_FOCUS_CATEGORIES_EXTENDED)
    if category is None:
        return 0

    reason = _llm_notice(
        f"blocked: サブエージェント完了報告に縮退表明カテゴリ`{category}`が検出された。"
        "報告本文の該当箇所を修正するか、実装未完遂として作業を継続すること。"
        "再送する場合は訂正内容だけでなく、当初の完了報告本文全体を再掲したうえで"
        "該当箇所のみを書き換えること（本フックのブロックでメイン側は本文を保持していないため）。"
        "調査・レビュー系タスクの完了報告で規範文書の引用のため縮退表明フレーズを"
        "本文へ含める必要がある場合は、`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従い"
        "カテゴリ識別子または節名で間接参照する形へ書き換えること。",
        tag="block",
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
