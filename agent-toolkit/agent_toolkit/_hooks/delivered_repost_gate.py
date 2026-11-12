"""`send_to_user`の送達が成功した後の応答本文が、送達済みの内容の再掲になる終了を遮断する。

規範（`rules-main.claude-code.md`「ターンの終え方」）は、送達後に待機・完了を問わず`…`だけで閉じることを求める。
最後の応答本文が`…`だけでなく、句点・終止符・改行で区切った文が2つ以上ある場合を再掲と判定する。1文以下は通す。
判定はtranscriptの観測（同じターンの送達成功の有無と本文の形）だけで決まり、`…`だけで閉じれば回復できる。
意味の同一性は判定しないため、2文以上の新しい内容も止まる。その内容は`send_to_user`で送れば残る。
非同期の待機が残るStopにも適用し、言語判定（`response_language_advisor`）とは独立に動く。

通知を遮断にする根拠: 警告では、送達した内容を応答本文へ再掲する事象が止まらなかった。
遮断された主体は応答本文を`…`だけにするか、新しい内容を`send_to_user`で送るだけで回復できるため、
`agent-basics:writing-standards`の`references/claude-hooks-block-warn-eligibility.md`の遮断の条件に当たる。
連続blockの上限は`stop.py`が管理する。

委譲先での実行可否: ユーザーへ送達するメインだけを対象とし、サブエージェントと委譲先、`send_to_user`の無いCodexは判定しない。
"""

import re

from agent_toolkit._hooks import notice

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from agent_toolkit._common import transcript
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.user_response_advisor import latest_human_turn

_SENTENCE_BREAK = re.compile(r"[。！？!?\n]|\.(?=\s|$)")

_block_notice = notice.block_formatter("delivered_repost_gate")


def _last_text_after_delivery(entries: list[dict]) -> str:
    """同じターンで最後に送達が成功した後の、最後の応答本文を返す。送達が無ければ空文字列を返す。"""
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
                elif block.get("type") == "tool_use" and str(block.get("name", "")).endswith(
                    transcript.SEND_TO_USER_TOOL_SUFFIX
                ):
                    sent_ids.add(str(block.get("id", "")))
            elif (
                block.get("type") == "tool_result"
                and block.get("tool_use_id") in sent_ids
                and block.get("is_error") is not True
            ):
                delivered, last = True, ""
        if delivered and texts:
            last = "".join(texts)
    return last


def is_repost(text: str) -> bool:
    """本文が`…`だけではなく、文を2つ以上含むかを返す。"""
    body = text.strip()
    if not body.strip("… \t\r\n"):
        return False
    return sum(1 for sentence in _SENTENCE_BREAK.split(body) if sentence.strip()) >= 2


def evaluate(payload_text: str) -> tuple[str, str]:
    """送達成功後に2文以上の応答本文でターンを終える場合に遮断する。"""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    path = payload.get("transcript_path")
    if is_codex_payload(payload) or not agent_id.is_main_agent_context(payload) or not isinstance(path, str) or not path:
        return "approve", ""
    lines = transcript.read_transcript_lines(path)
    turn = latest_human_turn(lines) if lines is not None else None
    if turn is None or not is_repost(_last_text_after_delivery(turn[1])):
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_delivered_repost", {})
    return "block", _block_notice(
        "`send_to_user`で送達済みの後に、応答本文が2文以上ある。送達済みの内容の言い換え・要約・抜粋の再掲は不要である。",
        fix="応答本文を`…`だけにして閉じよ。新しく伝える内容があれば`send_to_user`で送れ。",
    )
