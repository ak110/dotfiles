"""`send_to_user`の呼出しと送達後の応答を、同じターンの記録で検査する。

規範（`atk-mode`の`playbooks/host-claude-code.md`「ターンの終え方」）は、送達後に待機・完了を問わず`…`だけで閉じることを求める。
通常の対話では1文の送達確認も遮断する。process-loopの統括とrun-skillでは、担当工程完了の1文を許す。
同じターン（直近の入力以降）の直近の送達が失敗したら、未表示の本文で終了できる。非同期の待機が残るStopにも適用する。
`send_to_user`を使えるメインには、現在の入力に限らず常に`send_to_user`を求める（記録全体に端末modの送達の指定の添付があるかで判定する）。
入力ごとの指定は圧縮の後や完了通知で始まるターンに添わず、通常本文へ戻る事象が続いたためである（2026-11-22、ユーザー指摘）。

通知を遮断にする根拠: 警告では、送達した内容を応答本文へ再掲する事象が止まらなかった。
遮断された主体は応答本文を`…`だけにするか、新しい内容を`send_to_user`で送るだけで回復できるため、
`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`の遮断の条件に当たる。
連続blockの上限は`stop.py`が管理する。

委譲先での実行可否: ユーザーへ送達するメインだけを対象とし、サブエージェントと委譲先、`send_to_user`の無いCodexは判定しない。
"""

import json
import os
import re

from agent_toolkit._common.agents_server_tools import MCP_NAMESPACES
from agent_toolkit._hooks import notice

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.message_format import starts_with_auto_element
lazy from agent_toolkit._common.process_loop_session import is_process_loop_session
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.run_skill_report_gate import RUN_ID_ENV
lazy from agent_toolkit._hooks.user_response_advisor import _human_prompt, _input_text, latest_human_turn

# 再開した会話の記録では改名前のツール名も送達の根拠になる。
_SEND_TO_USER_TOOLS = {prefix + "send_to_user" for prefix in MCP_NAMESPACES} | {
    "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user",
    "mcp__agent-toolkit__send_to_user",
}
_SENTENCE_BREAK = re.compile(r"[。！？!?\n]|\.(?=\s|$)")
_SEND_CONTRACT = '<atk-auto source="send-to-user-contract" kind="notice">'
_SEND_CALL = "tools['mcp__plugin_agent-toolkit_agents_server__send_to_user']({message})"

_block_notice = notice.block_formatter("delivered_repost_gate")


def is_send_to_user_tool(name: object) -> bool:
    """別のMCPの同名ツールを、このプラグインの送達と取り違えない。"""
    return isinstance(name, str) and name in _SEND_TO_USER_TOOLS


def _send_required(entries: list[dict]) -> bool:
    """端末modの送達の指定の添付が、圧縮の前を含む記録全体に1回でもあるかを返す。

    指定は端末のメインで送達ツールが登録済みの時だけ添えられ、圧縮の後や完了通知で始まるターンには添わない。
    """
    for entry in entries:
        attachment = entry.get("attachment") if entry.get("type") == "attachment" else None
        if (
            isinstance(attachment, dict)
            and attachment.get("type") == "hook_additional_context"
            and attachment.get("hookEvent") == "UserPromptSubmit"
            and attachment.get("hookName") == "prompt.submit"
            and isinstance(content := attachment.get("content"), list)
            and any(isinstance(item, str) and item.startswith(_SEND_CONTRACT) for item in content)
        ):
            return True
    return False


def _delivery_report(entries: list[dict]) -> tuple[str, bool]:
    """送達成功後の最後の本文と、成功または失敗の結果が記録されたかを返す。"""
    sent_ids: set[str] = set()
    completed = False
    delivered = False
    last = ""
    for entry in entries:
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if entry.get("type") == "assistant":
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    texts.append(block["text"])
                elif (
                    block.get("type") == "tool_use"
                    and is_send_to_user_tool(block.get("name"))
                    and isinstance(identifier := block.get("id"), str)
                    and identifier
                ):
                    sent_ids.add(identifier)
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id", "")) in sent_ids:
                completed = True
                # 途中報告の後で次の送達が失敗したら、未表示の本文で終了できるようにする。
                delivered, last = block.get("is_error") is not True, ""
        if delivered and texts:
            last = "".join(texts)
    return last, completed


def _current_turn(entries: list[dict]) -> list[dict]:
    """直近の入力（人間の発話、完了通知、自動入力）より後の記録を返す。

    同じ人間入力の後でも、完了通知などで始まる別ターンの送達を現ターンの結果と混同しない。
    """
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if (text := _input_text(entry)) is not None and (
            _human_prompt(entry, text) is not None
            or starts_with_auto_element(text)
            or text.lstrip().startswith("<task-notification")
        ):
            return entries[index + 1 :]
    return entries


def _conversation_entries(lines: list[str]) -> list[dict]:
    """sidechainを除いた会話全体の記録を返す。"""
    entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and not entry.get("isSidechain"):
            entries.append(entry)
    return entries


def evaluate(payload_text: str) -> tuple[str, str]:
    """指定された送達の結果がない通常本文、または送達済み本文の再掲を拒む。"""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    path = payload.get("transcript_path")
    if (
        is_codex_payload(payload)
        or is_antigravity_payload(payload)
        or not agent_id.is_main_agent_context(payload)
        or not isinstance(path, str)
        or not path
    ):
        return "approve", ""
    lines = transcript.read_transcript_lines(path)
    turn = latest_human_turn(lines) if lines is not None else None
    body, completed = _delivery_report(_current_turn(turn[1])) if turn else ("", False)
    if (
        turn
        and lines is not None
        and not completed
        and isinstance(last := payload.get("last_assistant_message"), str)
        and last.replace("…", "").strip()
        and _send_required(_conversation_entries(lines))
    ):
        return "block", _block_notice(
            "`send_to_user` を使える会話で、このターンに `send_to_user` の結果が無い。通常本文での終了を止めた。",
            fix=f"`REPL` では `await {_SEND_CALL}` を呼べ。ツールを直接呼べる場合は "
            "`mcp__plugin_agent-toolkit_agents_server__send_to_user` へ本文を渡せ。",
        )
    if not body.replace("…", "").strip():
        return "approve", ""
    allows_completion_sentence = is_process_loop_session(session_id, os.environ) or bool(os.environ.get(RUN_ID_ENV))
    if allows_completion_sentence and sum(1 for sentence in _SENTENCE_BREAK.split(body) if sentence.strip()) < 2:
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_delivered_repost", {})
    return "block", _block_notice(
        "`send_to_user`の送達後に、応答本文が残っている。送達の確認や要約は不要である。",
        fix="応答本文を`…`だけにして閉じよ。新しく伝える内容があれば`send_to_user`で送れ。",
    )
