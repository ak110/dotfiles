"""人間の発話の後に可視の応答本文が無いStopを遮断する。

判定情報はtranscriptの要素種別とtextブロックの有無から確定する。
回答の意味を判定せず、拡張思考とツール呼び出しを可視本文へ数えない。
同じターンで回答または受領の説明を本文へ出力すれば解除できる。
Stopの連続遮断上限は集約入口に任せ、独自の状態や上限を持たない。

委譲先での実行可否: 利用者への応答責務を持つメインだけを対象とし、
agent_idと委譲先の環境印でサブエージェントと委譲先を除く。
"""

import json
import pathlib

from agent_toolkit._hooks import agent_id, notice, stop_gate, transcript

_block_notice = notice.block_formatter("user_response_advisor")


def evaluate(payload_text: str) -> tuple[str, str]:
    """最新の人間の発話に本文が無い場合だけ、本文の出力を求める。"""
    resolved = stop_gate.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if not agent_id.is_main_agent_context(payload):
        return "approve", ""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return "approve", ""
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
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
            text = transcript.assistant_text(entry.get("message"))
            if any(not char.isspace() and char != "…" for char in text):
                needs_response = False

    if not needs_response:
        return "approve", ""
    stop_gate.append_stop_log(session_id, "block_missing_user_response", {})
    return "block", _block_notice(
        "人間の発話の後に、利用者へ表示される応答本文が無い。拡張思考の中の記述は利用者へ表示されない。",
        fix="利用者の発話への回答または受領の説明を、発話本文として出力する。",
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
    return prompt.lstrip().startswith(("<atk-auto", "<agent-toolkit-auto-inserted"))


def main(payload_text: str) -> int:
    """本文欠落のStop判定をhook応答として返す。"""
    decision, body = evaluate(payload_text)
    print(json.dumps({"decision": "block", "reason": body} if decision == "block" else {}, ensure_ascii=False))
    return 0
