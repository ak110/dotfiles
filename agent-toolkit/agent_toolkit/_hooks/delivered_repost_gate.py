"""`send_to_user`の呼出しと送達後の応答を、同じターンの記録で検査する。  pyfltr: allow-comment

送達に失敗した場合は未表示の情報を通常本文で伝えられる。
`send_to_user`を使えるメインには、現在の入力に限らず常に`send_to_user`を求める（記録全体に端末modの送達の指定の添付があるかで判定する）。
入力ごとの指定は圧縮の後や完了通知で始まるターンに添わず、通常本文へ戻る事象が続いたためである（2026-11-22、ユーザー指摘）。
連続blockの上限は`stop.py`が管理する。

委譲先での実行可否: ユーザーへ送達するメインだけを対象とし、サブエージェントと委譲先、`send_to_user`の無いCodexは判定しない。
"""

import json
from collections.abc import Iterator
lazy import hashlib
lazy import os

from agent_toolkit._common.agents_server_tools import is_send_to_user_tool
from agent_toolkit._hooks import notice
lazy from agent_toolkit._common import session_state, transcript
lazy from agent_toolkit._common.completion_exit import ui_receipt
lazy from agent_toolkit._common.json_lines import split_joined_line
lazy from agent_toolkit._common.message_format import starts_with_auto_element
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import stop_session as _stop_session
lazy from agent_toolkit._hooks.background_tasks_notifications import _delivered_user_body
lazy from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks.transcript_scan import read_stop_entries
lazy from agent_toolkit._hooks.user_response_advisor import _human_prompt, _input_text

_SEND_CONTRACT = '<atk-auto source="send-to-user-contract" kind="notice">'
_SEND_CALL = "tools['mcp__plugin_agent-toolkit_agents_server__send_to_user']({message})"

_block_notice = notice.block_formatter("delivered_repost_gate")
_delivery_notice = notice.formatter("delivered_repost_gate")


def send_fix() -> str:
    return (
        f"必要な項目を同じ本文にそろえ、`REPL`では`await {_SEND_CALL}`、直接呼べる場合は"
        "`mcp__plugin_agent-toolkit_agents_server__send_to_user`で1回送達してください。"
        "送達に失敗した場合は診断を解消し、送れていない内容だけを送り直してください。"
        "同じ入力に対して全項目の送達に成功し、新しい成果もなければ、再送・再掲せず `…` だけで終えてください。"
        "新しい入力や成果があれば、今回必要な内容だけを送達してください。"
    )


def send_required(entries: list[dict]) -> bool:
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


def delivered_messages(entries: list[dict]) -> Iterator[tuple[int, int, str]]:
    calls: dict[str, tuple[int, str]] = {}
    for index, entry in enumerate(entries):
        message = entry.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                continue
            if (
                entry.get("type") == "assistant"
                and block.get("type") == "tool_use"
                and is_send_to_user_tool(block.get("name"))
                and isinstance(identifier := block.get("id"), str)
                and isinstance(args := block.get("input"), dict)
                and isinstance(text := args.get("message"), str)
            ):
                calls[identifier] = index, text
            elif (
                entry.get("type") == "user"
                and block.get("type") == "tool_result"
                and block.get("is_error") is not True
                and isinstance(identifier := block.get("tool_use_id"), str)
                and identifier in calls
            ):
                sent_at, sent_text = calls.pop(identifier)
                if ui_receipt(entry, block) is not None:
                    yield sent_at, index, sent_text


def _latest_delivery_error(entries: list[dict]) -> bool | None:
    sends: set[str] = set()
    latest_error: bool | None = None
    for entry in entries:
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if (
                entry.get("type") == "assistant"
                and block.get("type") == "tool_use"
                and is_send_to_user_tool(block.get("name"))
                and isinstance(identifier := block.get("id"), str)
                and identifier
            ):
                sends.add(identifier)
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id", "")) in sends:
                latest_error = block.get("is_error") is True
    return latest_error


def turn_prompt(entry: dict) -> str | None:
    attachment = entry.get("attachment")
    if (
        entry.get("type") == "attachment"
        and isinstance(attachment, dict)
        and attachment.get("commandMode") == "task-notification"
    ):
        prompt = attachment.get("prompt")
        return prompt if isinstance(prompt, str) else None
    return _input_text(entry) or _delivered_user_body(entry)


def turn_boundary(entry: dict) -> bool:
    if entry.get("isCompactSummary") is True or (entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"):
        return True
    return (text := turn_prompt(entry)) is not None and (
        _human_prompt(entry, text) is not None
        or starts_with_auto_element(text)
        or text.lstrip().startswith("<task-notification")
    )


def _current_turn(entries: list[dict]) -> list[dict]:
    for index in range(len(entries) - 1, -1, -1):
        if turn_boundary(entries[index]):
            return entries[index + 1 :]
    return entries


def _progress_turn(path: str, session_id: str, *, reuse_cursor: bool = True) -> list[dict]:
    try:
        stat = os.stat(path)
    except OSError, ValueError:
        return []
    identity = [path, stat.st_dev, stat.st_ino]
    cursor = session_state.read_state(session_id).get("delivery_progress_cursor") if reuse_cursor else None
    if not isinstance(cursor, dict):
        cursor = {}
    offset, boundary = cursor.get("offset"), cursor.get("boundary")
    if (
        cursor.get("identity") != identity
        or not isinstance(offset, int)
        or not isinstance(boundary, int)
        or not 0 <= boundary <= offset <= stat.st_size
    ):
        cursor, offset, boundary = {}, 0, 0
    required = cursor.get("required") is True
    anchor = max(0, offset - 128)
    start = min(boundary, anchor) if required else anchor
    data = transcript.read_transcript_bytes(path, offset=start)
    if data is None:
        return []
    if offset and hashlib.sha256(data[anchor - start : offset - start]).hexdigest() != cursor.get("anchor"):
        data = transcript.read_transcript_bytes(path)
        if data is None:
            return []
        start, offset, boundary, required, cursor = 0, 0, 0, False, {}

    turn: list[dict] = []
    position = max(boundary, start) if required else offset
    complete = offset
    for raw in data[position - start :].split(b"\n"):
        if not raw and position == start + len(data):
            break
        position += len(raw) + 1
        finished = position <= start + len(data)
        if finished:
            complete = max(complete, position)
        try:
            entry = json.loads(raw.decode("utf-8"))
        except ValueError, RecursionError:
            joined = split_joined_line(raw)
            entry = joined[1] if joined is not None else None
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        if not required and send_required([entry]):
            if not finished:
                return _current_turn(_conversation_entries(transcript.read_transcript_lines(path) or []))
            required = True
            if offset > boundary:
                return _progress_turn(path, session_id, reuse_cursor=False)
        if turn_boundary(entry):
            if finished:
                boundary = position
            turn.clear()
        else:
            turn.append(entry)
    state = {
        "identity": identity,
        "offset": complete,
        "boundary": boundary,
        "required": required,
        "anchor": hashlib.sha256(data[max(0, complete - start - 128) : complete - start]).hexdigest(),
    }
    if state != cursor:
        session_state.update_state(session_id, lambda old: {**old, "delivery_progress_cursor": state})
    return turn if required else []


def _conversation_entries(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and not entry.get("isSidechain"):
            entries.append(entry)
    return entries


def current_turn_delivery(entries_path: str) -> tuple[list[dict], bool | None]:
    entries = [entry for entry in read_stop_entries(entries_path) if not entry.get("isSidechain")]
    return entries, _latest_delivery_error(_current_turn(entries))


def _visible_response_id(entries: list[dict], tool_use_id: str) -> str | None:
    visible: set[str] = set()
    response_id = None
    for entry in reversed(entries):
        if entry.get("isApiErrorMessage"):
            break
        message = entry.get("message")
        if entry.get("type") != "assistant" or not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        identifier = message.get("id")
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                if not isinstance(identifier, str) or not identifier:
                    return None
                response_id = identifier
            if (
                isinstance(identifier, str)
                and block.get("type") == "text"
                and isinstance(body := block.get("text"), str)
                and body.strip()
            ):
                visible.add(identifier)
    return response_id if response_id in visible else None


def pretooluse_notice(payload: dict, session_id: str) -> str | None:
    path = payload.get("transcript_path")
    tool_use_id = payload.get("tool_use_id")
    if (
        not session_id
        or not isinstance(path, str)
        or not path
        or not isinstance(tool_use_id, str)
        or not tool_use_id
        or tool_use_id.startswith("repl_")
        or is_codex_payload(payload)
        or is_antigravity_payload(payload)
        or not agent_id.is_main_agent_context(payload)
    ):
        return None
    turn = _progress_turn(path, session_id)
    response_id = _visible_response_id(turn, tool_use_id)
    if response_id is None:
        return None
    latest_error = _latest_delivery_error(turn)
    if latest_error is True:
        return None

    def claim(state: dict) -> dict | None:
        ids = state.get("delivery_notice_response_ids")
        if not isinstance(ids, list):
            ids = []
        if response_id in ids:
            return None
        return {**state, "delivery_notice_response_ids": [*ids, response_id]}

    if session_state.update_state(session_id, claim):
        return _delivery_notice(
            "送達指定のある会話で、ツール呼び出しと同じ応答に通常本文があります。",
            fix="次の進捗報告や確認から、通常本文ではなく `send_to_user` を使ってください。",
            subject=response_id,
        )
    return None


def evaluate(
    payload_text: str, *, quiet_intermission: bool = False, delivery: tuple[list[dict], bool | None] | None = None
) -> tuple[str, str]:
    if quiet_intermission:
        return "approve", ""
    resolved = _stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    _, payload = resolved
    path = payload.get("transcript_path")
    if (
        is_codex_payload(payload)
        or is_antigravity_payload(payload)
        or not agent_id.is_main_agent_context(payload)
        or not isinstance(path, str)
        or not path
    ):
        return "approve", ""
    entries, latest_error = delivery if delivery is not None else current_turn_delivery(path)
    if (
        latest_error is None
        and isinstance(last := payload.get("last_assistant_message"), str)
        and last.replace("…", "").strip()
        and send_required(entries)
    ):
        return "block", _block_notice(
            "`send_to_user` を使える会話で、このターンに `send_to_user` の結果が無い。通常本文での終了を止めた。",
            fix=send_fix(),
        )
    return "approve", ""
