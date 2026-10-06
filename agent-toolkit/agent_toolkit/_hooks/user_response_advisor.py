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
`agent-toolkit:writing-standards`の`references/claude-hooks.md`「遮断・警告フックの成立条件」にある。

委譲先での実行可否: ユーザーへの応答責務を持つメインだけを対象とし、
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
            text = transcript.visible_assistant_text(entry.get("message"))
            if any(not char.isspace() and char != "…" for char in text):
                needs_response = False

    if not needs_response:
        return "approve", ""
    stop_gate.append_stop_log(session_id, "block_missing_user_response", {})
    return "block", _block_notice(
        "人間の発話の後に、応答本文（`text`）が無い。"
        "拡張思考（その要約を含む）が画面に表示されたかはエージェントから観測できないため、"
        "表示されないものとして扱い、応答を発話本文へ書く。",
        fix=(
            "そのターンでユーザーへ伝えるつもりだった判断の報告・確認結果・回答などを、発話本文として出力し直す。"
            "拡張思考やツール呼び出しの前に書いたつもりの内容も含め、受領や状況の説明だけで済ませない。"
        ),
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
