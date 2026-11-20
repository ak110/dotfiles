"""`send_to_user`の送達が成功した後の応答本文が、送達済みの内容の再掲になる終了を遮断する。

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
lazy from agent_toolkit._common.process_loop_session import is_process_loop_session
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.run_skill_report_gate import RUN_ID_ENV
lazy from agent_toolkit._hooks.user_response_advisor import latest_human_turn

# 再開した会話の記録では改名前のツール名も送達の根拠になる。
_SEND_TO_USER_TOOLS = {prefix + "send_to_user" for prefix in MCP_NAMESPACES} | {
    "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user",
    "mcp__agent-toolkit__send_to_user",
}
_SENTENCE_BREAK = re.compile(r"[。！？!?\n]|\.(?=\s|$)")

_block_notice = notice.block_formatter("delivered_repost_gate")


def _last_text_after_delivery(entries: list[dict]) -> str:
    """同じターンの直近の送達結果が成功なら、その後の最後の応答本文を返す。"""
    sent_ids: set[str] = set()
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
                elif block.get("type") == "tool_use" and block.get("name") in _SEND_TO_USER_TOOLS:
                    sent_ids.add(str(block.get("id", "")))
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in sent_ids:
                # 途中報告の後で次の送達が失敗したら、未表示の本文で終了できるようにする。
                delivered, last = block.get("is_error") is not True, ""
        if delivered and texts:
            last = "".join(texts)
    return last


def evaluate(payload_text: str) -> tuple[str, str]:
    """送達成功後に余分な本文でターンを終える場合に遮断する。"""
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
    body = _last_text_after_delivery(turn[1]) if turn else ""
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
