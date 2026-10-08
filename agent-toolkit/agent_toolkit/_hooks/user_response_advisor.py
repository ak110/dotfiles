"""人間の発話の後に可視の応答本文が無いStopを遮断する。

判定情報はtranscriptの要素種別と、textブロックと`send_to_user`の`message`の有無から確定する。
拡張思考の表示をエージェントから観測できないため、応答はユーザーへ届く本文の有無で判定する。
ツール呼び出しより前の地の文が要約へ置き換わる事象をこの判定で検出することはしない。発話したつもりで届いていない事象は
発話を契機とする判定では捉えられず、原文を`send_to_user`で運ぶ規範とツールで扱う（2026年10月6日、ユーザーの判断）。
回答の意味を判定せず、拡張思考とツール呼び出しは本文へ数えない。
同じターンで可視本文を出力すれば解除できる。通知は伝える予定だった内容の再出力を求める。
Stopの連続遮断上限は共通のStop処理に任せ、独自の状態や上限を持たない。

遮断とする根拠: 発話本文を可視の本文へ置く規範を加えた後も同じ欠落が再発した。
本文の欠落はエージェントの明らかな行動誤りで、判定はtranscriptから機械的に確定でき、
遮断で失うのは本文を出力し直す1回の応答だけである。基準は
`agent-toolkit:writing-standards`の`references/claude-hooks-block-warn.md`「遮断と警告の選択」にある。

委譲先での実行可否: ユーザーへの応答責務を持つメインだけを対象とし、
agent_idと委譲先の環境印でサブエージェントと委譲先を除く。
"""

import json

from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common import transcript
from agent_toolkit._hooks import agent_id, notice
from agent_toolkit._hooks import stop_session as _stop_session
from agent_toolkit._hooks.host import is_codex_payload

_block_notice = notice.block_formatter("user_response_advisor")


def evaluate(payload_text: str) -> tuple[str, str]:
    """最新の人間の発話に本文が無い場合だけ、本文の出力を求める。"""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if not agent_id.is_main_agent_context(payload):
        return "approve", ""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return "approve", ""
    lines = transcript.read_transcript_lines(path)
    if lines is None:
        return "approve", ""

    needs_response = False
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        prompt = _input_text(entry)
        if prompt is not None:
            if "<task-notification>" in prompt:
                needs_response = False
            elif not entry.get("isMeta") and not _is_hook_input(prompt):
                needs_response = True
        elif entry.get("type") == "assistant":
            text = transcript.visible_assistant_text(entry.get("message"))
            if any(not char.isspace() and char != "…" for char in text):
                needs_response = False

    if not needs_response:
        return "approve", ""
    _stop_session.append_stop_log(session_id, "block_missing_user_response", {})
    delivery = "応答本文" if is_codex_payload(payload) else "mcp__agent-toolkit__send_to_user（無い環境では応答本文）"
    return "block", _block_notice(
        "人間の発話の後に、ユーザーへ届いた応答本文が無い。",
        fix=f"伝える予定だった回答・判断・確認結果を{delivery}で届ける。",
    )


def _input_text(entry: dict) -> str | None:
    """人間の発話とtask-notificationを識別する文字列入力を返す。"""
    if entry.get("type") == "user":
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        return content if isinstance(content, str) else None
    if entry.get("type") == "attachment":
        attachment = entry.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
            prompt = attachment.get("prompt")
            return prompt if isinstance(prompt, str) else None
    return None


def _is_hook_input(prompt: str) -> bool:
    """機械生成の境界を持つ入力を人間の発話から除く。"""
    return _message_format.starts_with_auto_element(prompt)
