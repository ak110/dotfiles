"""記録からの証拠イベント抽出（session_evidence_extract_claude）。"""

from __future__ import annotations

import json
import re
from typing import Any

from session_evidence_extract_common import _HANDBACK_TOOL, _HOOK_FAILURE_PREFIX, _SKILL_INVOCATION_PREFIX, _event
from session_evidence_extract_questions import _claude_answers_event, _claude_question_options, _PendingQuestion

from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.runtime_inserted import is_runtime_generated as _is_runtime_generated
from agent_toolkit._common.session_family import _text_blocks


def _failed_tool_events(entry: dict[str, Any]) -> list[dict[str, Any]]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    events: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("is_error") is not True:
            continue
        text = "\n".join(_text_blocks(block.get("content")))
        event = _event("failed-tool", text, tool=str(block.get("tool_use_id", "")))
        if event:
            event["diagnostic_last_line"] = _failure_diagnostic(text)
            events.append(event)
    return events


def _failure_diagnostic(text: str) -> str:
    """hookの失敗は通知本文を診断にし、通常の失敗は最後の本文行を返す。"""
    if _HOOK_FAILURE_PREFIX.match(text):
        body = _HOOK_FAILURE_PREFIX.sub("", text, count=1)
        return re.sub(r"</?[^>]+>", "", body).strip()
    return next(
        (line.strip() for line in reversed(text.splitlines()) if line.strip() and not re.fullmatch(r"</[^>]+>", line.strip())),
        "",
    )


def _completion_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    result = entry.get("toolUseResult")
    if isinstance(result, dict) and result.get("status") == "completed":
        identity = result.get("agentId") or result.get("taskId") or "unknown"
        summary = result.get("summary") or result.get("result") or "completed"
        return _event("agent-completion", f"{identity}: {summary}")

    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    for text in _text_blocks(message.get("content")):
        if "<task-notification>" in text and "<status>completed</status>" in text:
            return _event("agent-completion", text)
    return None


def _is_subagent_record(entries: list[dict[str, Any]]) -> bool:
    """記録全体が1件のサブエージェントの会話かを返す。"""
    conversation = [entry for entry in entries if entry.get("type") in {"user", "assistant"}]
    return bool(conversation) and all(entry.get("isSidechain") is True for entry in conversation)


def _extract_claude(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """Claude Code形式を由来別の共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    pending_claude_questions: dict[str, _PendingQuestion] = {}
    tool_uses: dict[str, tuple[str, str]] = {}
    subagent_record = _is_subagent_record(entries)
    last_work_position: tuple[int, int] | None = None
    for line, entry in zip(lines, entries, strict=True):
        message = entry.get("message")
        if isinstance(message, dict) and entry.get("type") == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                for block_index, block in enumerate(content):
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        tool_uses[block["id"]] = (
                            str(block.get("name", "")),
                            json.dumps(block.get("input"), ensure_ascii=False, sort_keys=True),
                        )
                        name = str(block.get("name", ""))
                        if name != _HANDBACK_TOOL and not name.endswith(_transcript.SEND_TO_USER_TOOL_SUFFIX):
                            last_work_position = (line, block_index)
        for event in _claude_entry_events(entry, line, pending_claude_questions, subagent_record):
            if event.get("kind") == "failed-tool":
                tool_name, operation = tool_uses.get(str(event.get("tool", "")), ("", ""))
                event["tool_name"] = tool_name
                event["operation"] = operation
            event.setdefault("line", line)
            _set_entry_timestamp(event, entry)
            events.append(event)
    # Claude Code形式の本文は途中発話と最終応答を区別する標識を持たない。
    # 送信は報告そのものとして扱う。後続に作業ツールがある本文だけを、同一エントリ内の順序も含めて途中発話にする。
    for event in events:
        position = (event["line"], event.pop("_content_index", -1))
        if (
            last_work_position is not None
            and event.get("kind") == "assistant"
            and not event.get("handback")
            and position <= last_work_position
        ):
            event["phase"] = "commentary"
    return events


def _set_entry_timestamp(event: dict[str, Any], entry: dict[str, Any]) -> None:
    """イベントへ、由来するエントリの時刻を付ける。

    所要時間の区間の境界を、消費側が`--detail`の追加照会なしで確定できるようにする。
    時刻を持たないエントリではJSONのnullを付け、消費側が項目の有無を分岐せず扱えるようにする。
    """
    timestamp = entry.get("timestamp")
    event.setdefault("timestamp", timestamp if isinstance(timestamp, str) and timestamp else None)


def _claude_entry_events(
    entry: dict[str, Any],
    line: int,
    pending_claude_questions: dict[str, _PendingQuestion],
    subagent_record: bool = False,
) -> list[dict[str, Any]]:
    """Claude Codeの1エントリから共通イベントを取得する。

    メイン記録に混在するサブエージェントのエントリは完了報告だけを残す。
    記録全体が1件のサブエージェントの会話である場合は、走査対象そのものであるため通常のエントリとして扱う。
    """
    events: list[dict[str, Any]] = []
    if entry.get("isSidechain") is True and not subagent_record:
        completion = _completion_event(entry)
        if completion:
            events.append(completion)
        return events

    entry_type = entry.get("type")
    message = entry.get("message")
    user_texts: list[str] | None = None
    if isinstance(message, dict) and entry_type == "user" and message.get("role") == "user":
        user_texts = _text_blocks(message.get("content"))
        skill_invocation = next((text for text in user_texts if text.startswith(_SKILL_INVOCATION_PREFIX)), None)
        if skill_invocation is not None:
            event = _event("skill-invocation", skill_invocation.splitlines()[0])
            if event:
                events.append(event)
            return events

    result = entry.get("toolUseResult")
    is_interrupt = entry.get("isInterrupt") is True or entry.get("type") == "interrupt" or entry.get("subtype") == "interrupt"
    if is_interrupt:
        interrupt = _event("interrupt", json.dumps(entry, ensure_ascii=False))
        events.append(interrupt or {"kind": "interrupt", "text": "interrupt"})

    attachment = entry.get("attachment")
    if entry_type == "attachment" and isinstance(attachment, dict):
        origin = attachment.get("origin")
        prompt = attachment.get("prompt")
        if (
            attachment.get("type") == "queued_command"
            and isinstance(origin, dict)
            and origin.get("kind") == "human"
            and attachment.get("commandMode") != "task-notification"
            and isinstance(prompt, str)
        ):
            event = _event("user", prompt)
            if event:
                events.append(event)
    if isinstance(message, dict):
        role = message.get("role")
        if entry_type == "user" and role == "user":
            runtime_generated = _is_runtime_generated(entry)
            for text in user_texts or ():
                event = _event("user", text)
                if event and not event["text"].startswith("<task-notification>"):
                    if runtime_generated:
                        event["runtime_generated"] = True
                    events.append(event)
            answer_event = _claude_answers_event(result, message.get("content"), pending_claude_questions)
            if answer_event:
                events.append(answer_event)
            events.extend(_failed_tool_events(entry))
        elif entry_type == "assistant" and role == "assistant":
            pending_claude_questions.update(
                {
                    call_id: _PendingQuestion(line, options)
                    for call_id, options in _claude_question_options(message.get("content")).items()
                }
            )
            # ユーザーへ届いた本文として、`text`ブロックに加えて`send_to_user`の呼び出しの`message`もイベントにする。
            content = message.get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            for block_index, block in enumerate(blocks if isinstance(blocks, list) else []):
                for text in _transcript.visible_text_blocks([block]):
                    event = _event("assistant", text)
                    if event:
                        event["_content_index"] = block_index
                        events.append(event)
            for text in _handback_messages(message.get("content")):
                event = _event("assistant", text)
                if event:
                    event["handback"] = True
                    events.append(event)

    completion = _completion_event(entry)
    if completion:
        events.append(completion)
    return events


def _handback_messages(content: Any) -> list[str]:
    """Claude Codeのサブエージェントが`SubagentHandback`で委譲元へ渡した報告本文を返す。"""
    if not isinstance(content, list):
        return []
    messages: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != _HANDBACK_TOOL:
            continue
        block_input = block.get("input")
        message = block_input.get("message") if isinstance(block_input, dict) else None
        if isinstance(message, str):
            messages.append(message)
    return messages
