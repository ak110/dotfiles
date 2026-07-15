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

named subagent（`agent_name`非空）でtranscript内のtool_use数が閾値以上ある場合、
メイン宛のSendMessage送付履歴（`name == "SendMessage"`かつ`input.to == "main"`）が
無い時にblockを返し、完了報告の能動送付（またはメイン受領）を促す。
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

# tool_use数がこの閾値未満のnamed subagentは短命扱いで送信検査対象外とする。
# 起動直後のOSエラー・単一ツール失敗など、能動送付を求めるほど作業が進んでいない
# ケースの誤検出を防ぐため、経験則的な下限として設定する。
_NAMED_SUBAGENT_MIN_TOOL_USES = 3


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM宛て通知メッセージを標準プレフィックス付きで整形する。"""
    return _llm_notice_base(body, _HOOK_ID, tag=tag)


def _named_subagent_missing_main_send(payload: dict) -> bool:
    """Named subagentがメイン宛SendMessageを送付していない場合に真を返す。

    判定条件:
    - `agent_name`フィールドが非空文字列（named subagent起動）
    - `transcript_path`が読み取り可能
    - 当該subagentのtranscript内assistant `tool_use`ブロック総数が閾値以上
    - `name == "SendMessage"`かつ`input.to == "main"`のtool_use呼び出しが1件も存在しない

    上記全てを満たす場合に真。foregroundの短命subagent等でtool_use数が閾値未満の場合、
    または既にメイン宛SendMessage送付済みの場合は偽を返す。
    transcript読み取り失敗時は偽を返す（fail-open）。
    """
    agent_name = payload.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name:
        return False
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return False
    try:
        raw = pathlib.Path(transcript_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    tool_use_count = 0
    sent_to_main = False
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_count += 1
            if block.get("name") == "SendMessage":
                inp = block.get("input")
                if isinstance(inp, dict) and inp.get("to") == "main":
                    sent_to_main = True
    if tool_use_count < _NAMED_SUBAGENT_MIN_TOOL_USES:
        return False
    return not sent_to_main


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
            "blocked: the subagent completion report is effectively empty or consists only of a `Skill` invocation."
            " Either re-delegate the task or append the full completion body."
            " When resubmitting, restate the entire original completion report along with the added/corrected"
            " content (the main agent does not retain the body across this hook's block).",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    # `is_empty_completion_report`が非文字列・実質空を既に捕捉するため、
    # ここではtypeガードのみを残す。
    if not isinstance(text, str):
        return 0

    match_result = _match_scope_escalation(text, categories=_STOP_FOCUS_CATEGORIES_EXTENDED)
    if match_result is not None:
        category, _matched = match_result
        reason = _llm_notice(
            f"blocked: subagent completion report matched scope-escalation category `{category}`."
            " Either revise the flagged text or continue the work as unfinished."
            " When resubmitting, restate the entire original completion report and rewrite only the flagged"
            " passage (the main agent does not retain the body across this hook's block)."
            " For investigation/review reports that must quote a scope-escalation phrase as a normative"
            " reference, follow `agent-toolkit:agent-standards` 'Avoiding context contamination' section and"
            " use the category identifier or section name for indirect reference instead of the raw phrase.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    if _named_subagent_missing_main_send(payload):
        reason = _llm_notice(
            "blocked: this named subagent finished without ever calling `SendMessage(to='main')`."
            " Named subagents launched with `run_in_background=true` must actively deliver the completion"
            " report to the main agent via `SendMessage(to='main', message=<full body>)`; waiting for the main"
            " agent to poll is treated as incomplete."
            " Send the completion report body to main now and then stop."
            " If this subagent was launched in the foreground and the main agent already received the return"
            " value directly, ignore this notice.",
            tag="block",
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
