"""処置の証拠がない警告を示し、送達または終了を一度止める。  pyfltr: allow-comment

対象は、最後の人間入力か会話の圧縮の後に届いた`kind="warn"`の通知である。
完了通知や機械生成の入力はターンを分けるが、その前に届いた未対応の警告を捨てないよう境界に数えない。
hookの添付（`hook_additional_context`・`hook_system_message`）だけから集める。
ツール結果の本文は、ツール自身の警告と、取得した資料や委譲先の報告に引用された同じ形の文字列を区別できないため数えない。
`send_to_user`の警告は、呼出しを識別できる送達ではPostToolUseの添付として届くため、添付の側で数える。
警告ごとに、既存操作が示す処置の証拠、有効な誤検出申告、または同じ警告を指す対応の説明を探す。
処置を機械で観測できない警告について、対応の説明の内容までは検査しない。
ユーザーへ届く次の操作（`send_to_user`と`AskUserQuestion`）はPreToolUseから`send_block`で、ターンの終わりはStopで止める。
処置の成否を機械で判定できる警告は、再発行だけでは通さない。
ほかの警告は、両方の経路を通じて同じ組を1回だけ止める。
組は警告の添付の記録の識別子で区別し、別の発話の後に同じ文面で届いた警告は新しい組として扱う。

通知を遮断にする根拠: hook通知の処置と対応行は規範で定めていたが
（`agent-toolkit:atk-mode`の`references/conduct.md`「方針が衝突する場合の優先順位」）、
ユーザーが2026年10月27日から11月25日までにhookの通知の無視を少なくとも6回指摘し、文章の規範だけでは止まらなかった。
効果を判定した再発防止のうち有効だったのは、誤った操作の時点で機械が止めて正しい代替を示す型だった（`agent-basics:bugfix`の`references/prevention.md`）。

委譲先での実行可否: Claude Codeの`Stop`で動く会話（メインと`agents_server`の委譲先）を対象とし、
`agent_id`を持つサブエージェントの終了と、記録の形が異なるCodex・Antigravityは判定しない。
"""

import json
lazy import hashlib
lazy import re

from agent_toolkit._hooks import notice
lazy from agent_toolkit._common import send_misdetection, session_state, transcript
lazy from agent_toolkit._common.agents_server_tools import is_send_to_user_tool
lazy from agent_toolkit._common.message_format import AUTO_ELEMENT_NAME_PATTERN
lazy from agent_toolkit._hooks import review_receipt
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.delivered_repost_gate import (
    _conversation_entries,
    _human_prompt,
    _input_text,
    delivered_messages,
)
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload

RESPONSE_PREFIX = "hook通知への対応:"
_STATE_KEY = "hook_notice_response_gate"
_HOOK_RECORD_TYPES = frozenset({"hook_additional_context", "hook_system_message"})
_SUMMARY_LENGTH = 60
_REVIEW_WARNING = "レビュー返却が指定した資料の取得を確認できないまま、採否を記録する操作である。"

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


def _warnings(text: str, resolved: set[tuple[str, str]] | None = None) -> list[str]:
    found = []
    for match in _warn_pattern().finditer(text):
        if resolved and send_misdetection.warning_pair(match[0]) in resolved:
            continue
        first = next((line.strip() for line in match["body"].splitlines() if line.strip()), "")
        summary = first if len(first) <= _SUMMARY_LENGTH else first[:_SUMMARY_LENGTH] + "…"
        identifier = hashlib.sha256(match[0].encode("utf-8")).hexdigest()[:12]
        found.append(f"{match['source']}: [{identifier}] {summary}")
    return found


def _responses(text: str) -> set[str]:
    names = set()
    for line in text.splitlines():
        marker = line.strip().lstrip("-*> \t")
        if marker.startswith(RESPONSE_PREFIX):
            target, separator, evidence = marker.removeprefix(RESPONSE_PREFIX).strip().partition(" → ")
            if target and separator and evidence.strip():
                names.add(target)
    return names


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


def _last_read(entries: list[dict], calls: set[str]) -> int:
    if not calls:
        return len(entries)
    pending = calls.copy()
    last = -1
    for index, entry in enumerate(entries):
        if entry.get("type") != "user" or not isinstance(message := entry.get("message"), dict):
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else ():
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("is_error") is not True
                and isinstance(call := block.get("tool_use_id"), str)
                and call in pending
            ):
                pending.remove(call)
                last = index
    return len(entries) if pending else last


def _scan(turn: list[dict], resolved: set[tuple[str, str]], current: str, *, last_review_read: int) -> dict[str, str]:
    warnings: dict[str, tuple[int, str]] = {}
    responses = {name: len(turn) for name in _responses(current)}
    for index, entry in enumerate(turn):
        attachment = entry.get("attachment") if entry.get("type") == "attachment" else None
        if isinstance(attachment, dict) and attachment.get("type") in _HOOK_RECORD_TYPES:
            identity = str(entry.get("uuid") or entry.get("timestamp") or attachment.get("toolUseID") or index)
            for text in _strings(attachment.get("content")):
                warnings.update((name, (index, identity)) for name in _warnings(text, resolved))
        if entry.get("type") == "assistant":
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else ():
                if isinstance(block, dict) and block.get("type") == "text":
                    for name in _responses(str(block.get("text", ""))):
                        responses[name] = max(responses.get(name, -1), index)
    for sent_at, _, text in delivered_messages(turn):
        for name in _responses(text):
            responses[name] = max(responses.get(name, -1), sent_at)
    return {
        name: identity
        for name, (position, identity) in warnings.items()
        if responses.get(name, -1) <= (max(position, last_review_read) if _REVIEW_WARNING in name else position)
    }


def _unanswered(
    payload: dict, session_id: str, *, current: str = "", claims: list[send_misdetection.Claim] | None = None
) -> list[str] | None:
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
    resolved = send_misdetection.resolved(session_id) | {
        (item["check"], delivery_id) for item in claims or [] if (delivery_id := item["delivery_id"]) is not None
    }
    entries = _conversation_entries(lines)
    turn = _since_human_input(entries)
    last_read = _last_read(entries, review_receipt.current_read_calls(payload, session_id)) - (len(entries) - len(turn))
    warnings = _scan(turn, resolved, current, last_review_read=last_read)
    if not warnings:
        return None
    if any(_REVIEW_WARNING in name for name in warnings):
        return list(warnings)
    signature = hashlib.sha256(json.dumps(warnings, ensure_ascii=False).encode("utf-8")).hexdigest()

    def claim(state: dict) -> dict | None:
        if state.get(_STATE_KEY) == signature:
            return None
        return {**state, _STATE_KEY: signature}

    if not session_state.update_state(session_id, claim):
        return None
    return list(warnings)


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    listed = _unanswered(payload, session_id)
    if not listed:
        return "approve", ""
    return "block", _block_notice(
        f"このターンに届いた警告の処置を確認できないまま終えようとした。未対応の警告: {'；'.join(listed)}",
        fix=(
            "警告が求める操作を行ってください。"
            f"確認を補う場合は警告ごとに`{RESPONSE_PREFIX} <警告の表示名> → <結果か誤検出の根拠>`を書いてください"
            "（`send_to_user`を使う会話ではその本文に含める）。資料の取得を確認できる警告は、再発行だけでは通らない。"
        ),
    )


def send_block(
    payload: dict, session_id: str, tool_name: str, tool_input: dict, *, claims: list[send_misdetection.Claim] | None = None
) -> str | None:
    """未対応の警告が残るまま、ユーザーへ届く次の操作（報告の送達・質問）へ進む呼出しを一度止める。

    ターンの終わり（Stop）より前の、次にユーザーへ届く時点で処置させる。警告は作業の途中に届き、
    ターンの終わりの検査だけでは、警告を受けた操作から離れた時点でしか処置を促せなかった（2026年11月25日の分析）。
    `send_to_user`は対応行と有効な申告を、その警告にだけ用いる。送達の成功までは処置を記録しない。
    質問は選択肢の形が決まっているため、エージェントが対応行を先に`send_to_user`で送る形にする。 pyfltr: allow-comment
    """
    message = tool_input.get("message")
    listed = _unanswered(payload, session_id, current=message if isinstance(message, str) else "", claims=claims)
    if not listed:
        return None
    where = (
        f"本文に警告ごとの`{RESPONSE_PREFIX}`を書いてから送ってください"
        if is_send_to_user_tool(tool_name)
        else f"先に`send_to_user`で`{RESPONSE_PREFIX}`の行を送ってから、同じ質問を再発行してください"
    )
    return _block_notice(
        f"このターンに届いた警告の通知に未対応のまま、ユーザーへ届く操作へ進もうとした。未対応の警告: {'；'.join(listed)}",
        fix=(
            "警告が求める操作を行ってください。"
            f"確認を補う書式は`{RESPONSE_PREFIX} <警告の表示名> → <結果か誤検出の根拠>`です。"
            f"{where}。資料の取得を確認できる警告は、再発行だけでは通らない。"
        ),
    )
