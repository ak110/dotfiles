"""このターンに届いた警告のhook通知へ、`hook通知への対応:`の行を書かずに終える応答を一度止める。  pyfltr: allow-comment

対象は、最後の人間入力か会話の圧縮の後に届いた`kind="warn"`の通知である。
完了通知や機械生成の入力はターンを分けるが、その前に届いた未対応の警告を捨てないよう境界に数えない。
hookの添付（`hook_additional_context`・`hook_system_message`）だけから集める。
ツール結果の本文は、ツール自身の警告と、取得した資料や委譲先の報告に引用された同じ形の文字列を区別できないため数えない。
`send_to_user`の警告は、呼出しを識別できる送達ではPostToolUseの添付として届くため、添付の側で数える。
最後の警告より後のアシスタントの本文かツールの入力（`send_to_user`の本文、REPLのコード）に、行頭が`hook通知への対応:`の行があれば通す。
対応の中身（処置したか、誤検出の根拠か）は判定せず、行の有無だけを見る。
ユーザーへ届く次の操作（`send_to_user`と`AskUserQuestion`）はPreToolUseから`send_block`で、ターンの終わりはStopで止める。
止めるのは警告の組ごとに、両方の経路を通じて1回とする（再発行で通す）。
組は警告の添付の記録の識別子で区別し、別の発話の後に同じ文面で届いた警告は新しい組として扱う。

通知を遮断にする根拠: hook通知の処置と対応行は規範で定めていたが
（`agent-toolkit:atk-mode`の`references/conduct.md`「方針が衝突する場合の優先順位」）、
ユーザーが2026年10月27日から11月25日までにhookの通知の無視を少なくとも6回指摘し、文章の規範だけでは止まらなかった。
効果を判定した再発防止のうち有効だったのは、誤った操作の時点で機械が止めて正しい代替を示す型だった（`agent-basics:bugfix`の`references/prevention.md`）。
行の有無は記録から機械的に確定でき、1行の追記で回復できる。

委譲先での実行可否: Claude Codeの`Stop`で動く会話（メインと`agents_server`の委譲先）を対象とし、
`agent_id`を持つサブエージェントの終了と、記録の形が異なるCodex・Antigravityは判定しない。
"""

import json
lazy import hashlib
lazy import re

from agent_toolkit._hooks import notice
lazy from agent_toolkit._common import session_state, transcript
lazy from agent_toolkit._common.agents_server_tools import is_send_to_user_tool
lazy from agent_toolkit._common.message_format import AUTO_ELEMENT_NAME_PATTERN
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.delivered_repost_gate import _conversation_entries, _human_prompt, _input_text
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload

RESPONSE_PREFIX = "hook通知への対応:"
_STATE_KEY = "hook_notice_response_gate"
_HOOK_RECORD_TYPES = frozenset({"hook_additional_context", "hook_system_message"})
_SUMMARY_LENGTH = 60
_LISTED_LIMIT = 3

_block_notice = notice.block_formatter("hook_notice_response_gate")


def _warn_pattern() -> re.Pattern[str]:
    return re.compile(
        rf'<(?:{AUTO_ELEMENT_NAME_PATTERN})\s+source="(?P<source>[^"]*)"\s+kind="warn"[^>]*>(?P<body>[\s\S]*?)</(?:{AUTO_ELEMENT_NAME_PATTERN})>'
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item.get("text") if isinstance(item, dict) else item)]
    return []


def _warnings(text: str) -> list[str]:
    found = []
    for match in _warn_pattern().finditer(text):
        first = next((line.strip() for line in match["body"].splitlines() if line.strip()), "")
        summary = first if len(first) <= _SUMMARY_LENGTH else first[:_SUMMARY_LENGTH] + "…"
        found.append(f"{match['source']}: {summary}")
    return found


def _has_response(text: str) -> bool:
    return any(line.strip().lstrip("-*> \t").startswith(RESPONSE_PREFIX) for line in text.splitlines())


def _input_has_response(text: str) -> bool:
    """ツールの入力（REPLのコードなど）では、文字列の引用符の直後に始まる対応行も数える。 pyfltr: allow-comment"""
    return re.search(rf"(?:^|[\n`\"'])[ \t\-*>]*{RESPONSE_PREFIX}", text) is not None


def _since_human_input(entries: list[dict]) -> list[dict]:
    """最後の人間入力か会話の圧縮より後の記録を返す。 pyfltr: allow-comment"""
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry.get("isCompactSummary") is True or (
            entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"
        ):
            return entries[index + 1 :]
        if (text := _input_text(entry)) is not None and _human_prompt(entry, text) is not None:
            return entries[index + 1 :]
    return entries


def _scan(turn: list[dict]) -> tuple[list[str], list[str], int, int]:
    """警告の一覧、警告の添付の識別子、最後の警告と最後の対応行の位置を返す。位置が無ければ-1とする。 pyfltr: allow-comment"""
    warnings: list[str] = []
    identities: list[str] = []
    last_warning = last_response = -1
    for index, entry in enumerate(turn):
        attachment = entry.get("attachment") if entry.get("type") == "attachment" else None
        if isinstance(attachment, dict) and attachment.get("type") in _HOOK_RECORD_TYPES:
            event = str(attachment.get("hookEvent") or attachment.get("hookName") or "")
            if not event.startswith(("Stop", "SubagentStop")):
                for text in _strings(attachment.get("content")):
                    if found := _warnings(text):
                        warnings.extend(found)
                        identities.append(
                            str(entry.get("uuid") or entry.get("timestamp") or attachment.get("toolUseID") or index)
                        )
                        last_warning = index
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else ():
            if not isinstance(block, dict):
                continue
            if entry.get("type") == "assistant" and block.get("type") == "text":
                if _has_response(str(block.get("text", ""))):
                    last_response = index
            elif entry.get("type") == "assistant" and block.get("type") == "tool_use":
                arguments = block.get("input")
                if isinstance(arguments, dict) and any(
                    _input_has_response(value) for value in arguments.values() if isinstance(value, str)
                ):
                    last_response = index
    return warnings, identities, last_warning, last_response


def _unanswered(payload: dict, session_id: str) -> list[str] | None:
    """最後の警告より後に対応行が無ければ、未対応の警告の一覧を返す。

    同じ組には送達・質問・終了を通じて1回だけ返す。 pyfltr: allow-comment
    """
    path = payload.get("transcript_path")
    if (
        not session_id
        or payload.get("agent_id")
        or is_codex_payload(payload)
        or is_antigravity_payload(payload)
        or not isinstance(path, str)
        or not path
    ):
        return None
    lines = transcript.read_transcript_lines(path)
    if not lines:
        return None
    warnings, identities, last_warning, last_response = _scan(_since_human_input(_conversation_entries(lines)))
    if last_warning < 0 or last_response > last_warning:
        return None
    signature = hashlib.sha256(json.dumps([warnings, identities], ensure_ascii=False).encode("utf-8")).hexdigest()

    def claim(state: dict) -> dict | None:
        if state.get(_STATE_KEY) == signature:
            return None
        return {**state, _STATE_KEY: signature}

    if not session_state.update_state(session_id, claim):
        return None
    return list(dict.fromkeys(warnings))


def _listing(listed: list[str]) -> str:
    shown = "；".join(listed[:_LISTED_LIMIT])
    rest = f"；ほか{len(listed) - _LISTED_LIMIT}件" if len(listed) > _LISTED_LIMIT else ""
    return shown + rest


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    listed = _unanswered(payload, session_id)
    if not listed:
        return "approve", ""
    return "block", _block_notice(
        f"このターンに届いた警告の通知に、`{RESPONSE_PREFIX}`の行が無いまま終えようとした。未対応の警告: {_listing(listed)}",
        fix=(
            f"警告ごとに処置した結果か、処置しない根拠（誤検出など）を、`{RESPONSE_PREFIX}`で始まる1行にまとめて書いてから終えてください"
            "（`send_to_user`を使う会話ではその本文に含める）。同じ警告の組で再び止めることはしない。"
        ),
    )


def send_block(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> str | None:
    """未対応の警告が残るまま、ユーザーへ届く次の操作（報告の送達・質問）へ進む呼出しを一度止める。

    ターンの終わり（Stop）より前の、次にユーザーへ届く時点で処置させる。警告は作業の途中に届き、
    ターンの終わりの検査だけでは、警告を受けた操作から離れた時点でしか処置を促せなかった（2026年11月25日の分析）。
    `send_to_user`は本文に対応行があれば通す。
    質問は選択肢の形が決まっているため、エージェントが対応行を先に`send_to_user`で送る形にする。 pyfltr: allow-comment
    """
    message = tool_input.get("message")
    if isinstance(message, str) and _has_response(message):
        return None
    listed = _unanswered(payload, session_id)
    if not listed:
        return None
    where = (
        f"この本文に`{RESPONSE_PREFIX}`の行を加えて再送してください"
        if is_send_to_user_tool(tool_name)
        else f"先に`send_to_user`で`{RESPONSE_PREFIX}`の行を送ってから、同じ質問を再発行してください"
    )
    return _block_notice(
        f"このターンに届いた警告の通知に未対応のまま、ユーザーへ届く操作へ進もうとした。未対応の警告: {_listing(listed)}",
        fix=f"警告ごとに処置した結果か、処置しない根拠（誤検出など）を書いてください。{where}。同じ警告の組で再び止めることはしない。",
    )
