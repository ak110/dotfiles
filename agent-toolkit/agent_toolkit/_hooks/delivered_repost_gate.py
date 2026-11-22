"""`send_to_user`の呼出しと送達後の応答を、同じターンの記録で検査する。

規範（`atk-mode`の`playbooks/host-claude-code.md`「ターンの終え方」）は、送達後に待機・完了を問わず`…`だけで閉じることを求める。
通常の対話では1文の送達確認も遮断する。process-loopの統括とrun-skillでは、担当工程完了の1文を許す。
同じターンの直近の送達が失敗したら、未表示の本文で終了できる。非同期の待機が残るStopにも適用する。

通知を遮断にする根拠: 警告では、送達した内容を応答本文へ再掲する事象が止まらなかった。
遮断された主体は応答本文を`…`だけにするか、新しい内容を`send_to_user`で送るだけで回復できるため、
`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`の遮断の条件に当たる。
連続blockの上限は`stop.py`が管理する。

委譲先での実行可否: ユーザーへ送達するメインだけを対象とし、サブエージェントと委譲先、`send_to_user`の無いCodexは判定しない。
"""

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
_AFTER_DELIVERY = (
    "このターンで報告は送信済みです。同じ内容・送信の確認・要約を通常の応答本文へ書いてはいけません。"
    "新しい報告はsend_to_userで送り、質問は既存の質問手段を使う。"
    "次の操作がなければ本文を加えずにターンを終え、終了の合図が必要なら `…` を使える。"
)
_REMINDER_TAIL_BYTES = 262_144


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


def _delivery_report(entries: list[dict]) -> tuple[str, bool, bool]:
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
    return last, completed, delivered


def before_next_response(payload: dict, *, current_delivery: bool | None) -> str | None:
    """成功した現在の送達、または同じターンの後続ツールにだけ再提示する。"""
    if is_codex_payload(payload) or is_antigravity_payload(payload) or not agent_id.is_main_agent_context(payload):
        return None
    if current_delivery is not None:
        return _AFTER_DELIVERY if current_delivery else None
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        size = os.stat(path).st_size
    except OSError, ValueError:
        return None
    offset = max(0, size - _REMINDER_TAIL_BYTES)
    tail = transcript.read_transcript_bytes(path, offset=offset)
    if not tail or b"__send_to_user" not in tail:
        return None
    if offset:
        # 先頭は途中の行かもしれない。人間入力が末尾範囲に無ければ古い送達を推定しない。
        tail = tail.partition(b"\n")[2]
    turn = latest_human_turn(tail.decode("utf-8", errors="replace").split("\n"))
    return _AFTER_DELIVERY if turn and _delivery_report(turn[1])[2] else None


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
    body, completed, _ = _delivery_report(turn[1]) if turn else ("", False, False)
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
