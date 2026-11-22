"""`send_to_user`の呼出しと送達後の応答を、同じターンの記録で検査する。

同じターンで成功した送達と通常本文に同じ文がある場合に限り、再掲として差し戻す。
短い確認や新しく得た情報は、送達した文に含まれなければ差し戻さない。
送達に失敗した場合は未表示の情報を通常本文で伝えられる。
連続blockの上限は`stop.py`が管理する。

委譲先での実行可否: ユーザーへ送達するメインだけを対象とし、サブエージェントと委譲先、`send_to_user`の無いCodexは判定しない。
"""

import re

from agent_toolkit._common.agents_server_tools import MCP_NAMESPACES
from agent_toolkit._hooks import notice

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._common.message_format import starts_with_auto_element
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.user_response_advisor import _input_text, latest_human_turn

# 再開した会話の記録では改名前のツール名も送達の根拠になる。
_SEND_TO_USER_TOOLS = {prefix + "send_to_user" for prefix in MCP_NAMESPACES} | {
    "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user",
    "mcp__agent-toolkit__send_to_user",
}
_SENTENCE_BREAK = re.compile(r"[。！？!?\n]|\.(?=\s|$)")
_SEND_FOR_CURRENT_INPUT = '<atk-auto source="send-to-user-contract" kind="notice">'
_SEND_CALL = "tools['mcp__plugin_agent-toolkit_agents_server__send_to_user']({message})"

_block_notice = notice.block_formatter("delivered_repost_gate")


def is_send_to_user_tool(name: object) -> bool:
    """別のMCPの同名ツールを、このプラグインの送達と取り違えない。"""
    return isinstance(name, str) and name in _SEND_TO_USER_TOOLS


def _send_required(entries: list[dict]) -> bool:
    """現在の人間入力に対して実際に届いた関数hookの案内を確認する。"""
    for entry in reversed(entries):
        if (text := _input_text(entry)) is not None and (
            not entry.get("isMeta") or starts_with_auto_element(text) or text.lstrip().startswith("<task-notification")
        ):
            return False
        attachment = entry.get("attachment") if entry.get("type") == "attachment" else None
        if (
            isinstance(attachment, dict)
            and attachment.get("type") == "hook_additional_context"
            and attachment.get("hookEvent") == "UserPromptSubmit"
            and attachment.get("hookName") == "prompt.submit"
            and isinstance(content := attachment.get("content"), list)
            and any(isinstance(item, str) and item.startswith(_SEND_FOR_CURRENT_INPUT) for item in content)
        ):
            return True
    return False


def _delivery_report(entries: list[dict]) -> tuple[str, set[str], bool]:
    """最後の通常本文、表示した報告の文、送達結果の有無を返す。"""
    messages: dict[str, str] = {}
    delivered: set[str] = set()
    completed = False
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
                    inputs = block.get("input")
                    message_text = inputs.get("message") if isinstance(inputs, dict) else None
                    messages[identifier] = message_text if isinstance(message_text, str) else ""
            elif block.get("type") == "tool_result" and (identifier := str(block.get("tool_use_id", ""))) in messages:
                completed, last = True, ""
                if block.get("is_error") is not True:
                    delivered.update(_sentences(messages[identifier]))
        if delivered and texts:
            last = "".join(texts)
    return last, delivered, completed


def _sentences(text: str) -> set[str]:
    """空白・Markdownの外側の記号・文末記号の違いだけを無視する。"""
    return {line for part in _SENTENCE_BREAK.split(text) if (line := " ".join(part.split()).strip(">*_`#… "))}


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
    body, delivered, completed = _delivery_report(turn[1]) if turn else ("", set(), False)
    if (
        turn
        and not completed
        and isinstance(last := payload.get("last_assistant_message"), str)
        and last.replace("…", "").strip()
        and _send_required(turn[1])
    ):
        return "block", _block_notice(
            "この入力の案内で指定された `send_to_user` の結果が無い。通常本文での終了を止めた。",
            fix=f"`REPL` では `await {_SEND_CALL}` を呼べ。ツールを直接呼べる場合は "
            "`mcp__plugin_agent-toolkit_agents_server__send_to_user` へ本文を渡せ。呼出しに失敗した場合は通常本文で伝える。",
        )
    if not delivered.intersection(_sentences(body)):
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_delivered_repost", {})
    return "block", _block_notice(
        "`send_to_user`で送信済みの文が通常の応答本文にある。",
        fix="再掲した文を除き、新しく伝えることがなく、Claude Codeが可視の本文を求める場合は「…」で終える。",
    )
