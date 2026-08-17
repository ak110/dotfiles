#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude CodeとCodexのtranscriptから振り返り用の時系列証拠を抽出する。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

_MAX_TEXT_LENGTH = 2000
_SKILL_INVOCATION_PREFIX = "Base directory for this skill: "
STOP_ADVISOR_PREFIX = "[auto-generated: agent-toolkit/stop_advisor]"
SESSION_REVIEW_STARTED_MARKER = "[auto-generated: agent-toolkit/session-review-started]"
_FALLBACK_TEXT = (
    "transcript_pathを読み取れないため抽出証拠を生成できない。"
    "継承した会話履歴を評価し、取得できない範囲を未検証と明記すること。"
)
_Runtime = Literal["claude", "codex"]
_MANUAL_REVIEW_COMMANDS: dict[_Runtime, tuple[str, ...]] = {
    "claude": ("/session-review", "/agent-toolkit:session-review"),
    "codex": ("$session-review", "$agent-toolkit:session-review"),
}


def _clip(text: str) -> str:
    """証拠の意味を保ったまま巨大な本文を制限する。"""
    normalized = text.strip()
    if len(normalized) <= _MAX_TEXT_LENGTH:
        return normalized
    return normalized[:_MAX_TEXT_LENGTH] + "…[省略]"


def _text_blocks(content: Any, *, include_tool_results: bool = False) -> list[str]:
    """Message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            result.append(block["text"])
        elif include_tool_results and block_type == "tool_result":
            result.extend(_text_blocks(block.get("content"), include_tool_results=False))
    return result


def _codex_text_blocks(content: Any) -> list[str]:
    """Codex message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"input_text", "output_text", "text"} and isinstance(block.get("text"), str):
            result.append(block["text"])
    return result


def _event(kind: str, text: str, *, tool: str | None = None) -> dict[str, Any] | None:
    clipped = _clip(text)
    if not clipped:
        return None
    event: dict[str, Any] = {"kind": kind, "text": clipped}
    if tool:
        event["tool"] = tool
    return event


def _question_answers_event(pairs: list[tuple[str, list[str]]]) -> dict[str, Any] | None:
    """質問と回答を共通書式の単一userイベントへ変換する。"""
    sections: list[str] = []
    for question, answers in pairs:
        clipped_answers = [_clip(answer) for answer in answers]
        answer_text = "\n".join(clipped_answers)
        sections.append(f"質問: {_clip(question)}\n回答: {answer_text}")
    return _event("user", "\n".join(sections))


def _claude_question_call_ids(content: Any) -> set[str]:
    """AskUserQuestionのtool_use IDだけを取得する。"""
    if not isinstance(content, list):
        return set()
    return {
        block["id"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "AskUserQuestion"
        and isinstance(block.get("id"), str)
    }


def _claude_answers_event(
    result: Any,
    content: Any,
    pending_question_ids: set[str],
) -> dict[str, Any] | None:
    """対応するAskUserQuestionの結果だけを回答イベントへ変換する。"""
    if not isinstance(content, list):
        return None
    result_ids = {
        block["tool_use_id"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
    }
    matched_ids = pending_question_ids.intersection(result_ids)
    if not matched_ids:
        return None
    pending_question_ids.difference_update(matched_ids)
    if not isinstance(result, dict):
        return None
    answers = result.get("answers")
    if not isinstance(answers, dict) or not all(
        isinstance(question, str) and isinstance(answer, str) for question, answer in answers.items()
    ):
        return None
    return _question_answers_event([(question, [answer]) for question, answer in answers.items()])


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
            events.append(event)
    return events


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


def _extract_claude(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude Code形式を由来別の共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    pending_question_ids: set[str] = set()
    for entry in entries:
        if entry.get("isSidechain") is True:
            completion = _completion_event(entry)
            if completion:
                events.append(completion)
            continue

        entry_type = entry.get("type")
        message = entry.get("message")
        user_texts: list[str] | None = None
        if isinstance(message, dict) and entry_type == "user" and message.get("role") == "user":
            user_texts = _text_blocks(message.get("content"))
            if any(STOP_ADVISOR_PREFIX in text for text in user_texts):
                continue
            skill_invocation = next((text for text in user_texts if text.startswith(_SKILL_INVOCATION_PREFIX)), None)
            if skill_invocation is not None:
                event = _event("skill-invocation", skill_invocation.splitlines()[0])
                if event:
                    events.append(event)
                continue

        result = entry.get("toolUseResult")
        if (
            isinstance(result, dict)
            and isinstance(result.get("stdout"), str)
            and SESSION_REVIEW_STARTED_MARKER in result["stdout"]
        ):
            event = _event("session-review-started", SESSION_REVIEW_STARTED_MARKER)
            if event:
                events.append(event)

        is_interrupt = (
            entry.get("isInterrupt") is True or entry.get("type") == "interrupt" or entry.get("subtype") == "interrupt"
        )
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
                for text in user_texts or ():
                    event = _event("user", text)
                    if event and not event["text"].startswith("<task-notification>"):
                        events.append(event)
                answer_event = _claude_answers_event(result, message.get("content"), pending_question_ids)
                if answer_event:
                    events.append(answer_event)
                events.extend(_failed_tool_events(entry))
            elif entry_type == "assistant" and role == "assistant":
                pending_question_ids.update(_claude_question_call_ids(message.get("content")))
                for text in _text_blocks(message.get("content")):
                    event = _event("assistant", text)
                    if event:
                        events.append(event)

        completion = _completion_event(entry)
        if completion:
            events.append(completion)
    return events


def _codex_agent_message(payload: dict[str, Any]) -> str:
    """agent_messageの文字列互換を保ち、contentのblock配列を結合する。"""
    for key in ("message", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return "\n".join(_codex_text_blocks(payload.get("content")))


def _json_object(raw: Any) -> dict[str, Any] | None:
    """JSON文字列がobjectなら返し、破損又は別の値なら`None`を返す。"""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _codex_question_call(payload: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    """request_user_inputからcall_idごとの質問IDと質問文だけを取得する。"""
    if payload.get("name") != "request_user_input":
        return None
    call_id = payload.get("call_id")
    arguments = _json_object(payload.get("arguments"))
    if not isinstance(call_id, str) or arguments is None:
        return None
    raw_questions = arguments.get("questions")
    if not isinstance(raw_questions, list):
        return None
    questions: dict[str, str] = {}
    for raw_question in raw_questions:
        if not isinstance(raw_question, dict):
            continue
        question_id = raw_question.get("id")
        question = raw_question.get("question")
        if isinstance(question_id, str) and isinstance(question, str):
            questions.setdefault(question_id, question)
    return (call_id, questions) if questions else None


def _codex_question_output_event(
    payload: dict[str, Any],
    pending_questions: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    """対応する回答outputの位置で、call_id内の既知質問だけをuserイベントへ変換する。"""
    call_id = payload.get("call_id")
    if not isinstance(call_id, str):
        return None
    questions = pending_questions.pop(call_id, None)
    output = _json_object(payload.get("output"))
    if questions is None or output is None:
        return None
    raw_answers = output.get("answers")
    if not isinstance(raw_answers, dict):
        return None
    pairs: list[tuple[str, list[str]]] = []
    for question_id, question in questions.items():
        answer_data = raw_answers.get(question_id)
        if not isinstance(answer_data, dict):
            continue
        answers = answer_data.get("answers")
        if not isinstance(answers, list) or not all(isinstance(answer, str) for answer in answers):
            continue
        pairs.append((question, answers))
    return _question_answers_event(pairs) if pairs else None


def _codex_command_output(item: dict[str, Any]) -> str:
    """Codexコマンド実行項目から標準出力相当の本文を取得する。"""
    for key in ("aggregated_output", "output", "stdout"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_codex(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Codex rollout形式を共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    pending_questions: dict[str, dict[str, str]] = {}
    for entry in entries:
        entry_type = entry.get("type")
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if entry_type == "response_item" and payload_type == "function_call":
            question_call = _codex_question_call(payload)
            if question_call is not None:
                call_id, questions = question_call
                pending_questions[call_id] = questions
        elif entry_type == "response_item" and payload_type == "function_call_output":
            event = _codex_question_output_event(payload, pending_questions)
            if event:
                events.append(event)
        elif entry_type == "response_item" and payload_type == "message":
            role = payload.get("role")
            kind = "user" if role == "user" else "assistant" if role == "assistant" else None
            if kind is not None:
                for text in _codex_text_blocks(payload.get("content")):
                    event = _event(kind, text)
                    if event:
                        events.append(event)
        elif entry_type == "response_item" and payload_type == "agent_message":
            text = _codex_agent_message(payload)
            if text.lstrip().startswith("Message Type: FINAL_ANSWER"):
                event = _event("agent-completion", text)
                if event:
                    events.append(event)
        elif entry_type == "event_msg" and payload_type == "turn_aborted":
            event = _event("interrupt", json.dumps(payload, ensure_ascii=False))
            events.append(event or {"kind": "interrupt", "text": "turn_aborted"})
        elif entry_type == "event_msg" and payload_type == "item_completed":
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != "CommandExecution":
                continue
            status = item.get("status")
            output = _codex_command_output(item)
            if status == "completed" and SESSION_REVIEW_STARTED_MARKER in output:
                event = _event("session-review-started", SESSION_REVIEW_STARTED_MARKER)
            elif status == "failed":
                error = item.get("error")
                stderr = item.get("stderr")
                text = output or (stderr if isinstance(stderr, str) and stderr.strip() else "")
                if not text:
                    text = error if isinstance(error, str) and error.strip() else json.dumps(item, ensure_ascii=False)
                event = _event("failed-tool", text, tool="CommandExecution")
                if event:
                    command = item.get("command")
                    if isinstance(command, list) and all(isinstance(part, str) for part in command):
                        event["command"] = _clip(json.dumps(command, ensure_ascii=False))
                    exit_code = item.get("exit_code")
                    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                        event["exit_code"] = exit_code
            else:
                event = None
            if event:
                events.append(event)
    return events


def _is_manual_review_invocation(text: str, runtime: _Runtime) -> bool:
    stripped = text.strip()
    commands = _MANUAL_REVIEW_COMMANDS[runtime]
    if any(stripped == command or stripped.startswith(f"{command} ") for command in commands):
        return True
    return any(f"<command-name>{command}</command-name>" in stripped for command in commands)


def _finalize(events: list[dict[str, Any]], runtime: _Runtime) -> list[dict[str, Any]]:
    """手動・自動振り返り境界を適用し、最終結果と連番を確定する。"""
    manual_boundary = next(
        (
            index
            for index, event in enumerate(events)
            if event["kind"] == "user" and _is_manual_review_invocation(event["text"], runtime)
        ),
        len(events),
    )
    started_index = next(
        (index for index, event in enumerate(events) if event["kind"] == "session-review-started"),
        len(events),
    )
    automatic_boundary = len(events)
    if runtime == "codex" and started_index < len(events):
        automatic_boundary = next(
            (
                index
                for index in range(started_index - 1, -1, -1)
                if events[index]["kind"] == "user" and events[index]["text"].startswith(STOP_ADVISOR_PREFIX)
            ),
            len(events),
        )
    boundary = min(manual_boundary, automatic_boundary)
    events = [event for event in events[:boundary] if event["kind"] != "session-review-started"]

    for event in reversed(events):
        if event["kind"] == "assistant":
            event["kind"] = "final-result"
            break
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def extract(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """transcript形式を判定し、対象イベントを順序どおり抽出する。"""
    if not entries:
        return []
    runtime = _detect_runtime(entries)
    if runtime is None:
        return _fallback()
    return _finalize(_extract_for_runtime(entries, runtime), runtime)


def _detect_runtime(entries: list[dict[str, Any]]) -> _Runtime | None:
    """transcriptのエントリ形式から手動構文を解釈する実行系を返す。"""
    entry_types = {entry.get("type") for entry in entries}
    if entry_types & {"response_item", "event_msg"}:
        return "codex"
    if entry_types & {"user", "assistant", "interrupt", "attachment", "queue-operation"} or any(
        entry.get("isSidechain") is True or "toolUseResult" in entry for entry in entries
    ):
        return "claude"
    return None


def _extract_for_runtime(entries: list[dict[str, Any]], runtime: _Runtime) -> list[dict[str, Any]]:
    """確定したruntimeに対応する共通イベントへ変換する。"""
    return _extract_codex(entries) if runtime == "codex" else _extract_claude(entries)


def _fallback() -> list[dict[str, Any]]:
    return [{"sequence": 1, "kind": "fallback", "text": _FALLBACK_TEXT}]


def _load_entries(raw_path: str | None) -> list[dict[str, Any]] | None:
    """絶対パスのJSONLを読み、失敗時は`None`を返す。"""
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    entries: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                entries.append(parsed)
    except (json.JSONDecodeError, ValueError):
        return None
    return entries


def load_and_extract(raw_path: str | None) -> list[dict[str, Any]]:
    """絶対パスのJSONLを一度読み、抽出結果またはfallbackを返す。"""
    entries = _load_entries(raw_path)
    if entries is None:
        return _fallback()
    return extract(entries)


def has_session_review_started(raw_path: str | None) -> bool:
    """対応するtranscriptに振り返りの手動起動または起動確定標識があれば真を返す。"""
    entries = _load_entries(raw_path)
    if entries is None:
        return False
    runtime = _detect_runtime(entries)
    if runtime is None:
        return False
    events = _extract_for_runtime(entries, runtime)
    return any(
        event["kind"] == "session-review-started"
        or (event["kind"] == "user" and _is_manual_review_invocation(event["text"], runtime))
        for event in events
    )


def main(argv: list[str] | None = None) -> int:
    """証拠を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:] if argv is None else argv
    events = load_and_extract(args[0] if len(args) == 1 else None)
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
