#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude CodeとCodexのtranscriptから振り返り用の時系列証拠を抽出し、照会する。

既定モードは時系列イベントをJSONLで出力し、各イベントへ由来行の行番号`line`を付ける。
`--warn`・`--grep`・`--detail`の照会モードは、抽出結果に無い詳細をtranscriptから
1コマンドで取得するためのもので、都度のワンライナーによる再解析を置き換える。

本スクリプトは検査スクリプトではなくデータ抽出ツールであるため、
`agent-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と照会不能（モード併用・不正な正規表現・範囲外の行番号）を終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal, NamedTuple

_MAX_TEXT_LENGTH = 2000
_MAX_DETAIL_LENGTH = 8000
_OMISSION_MARK = "…[省略]"
_WARNING_PATTERN = re.compile("警告|warn", re.IGNORECASE)
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


class _Record(NamedTuple):
    """transcriptの1エントリと、その由来行の1始まり行番号・原文。"""

    line: int
    text: str
    entry: dict[str, Any]


def _clip(text: str, limit: int = _MAX_TEXT_LENGTH) -> str:
    """証拠の意味を保ったまま巨大な本文を制限する。"""
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + _OMISSION_MARK


class _DetailBudget:
    """1エントリの詳細出力が共有する残り文字数。

    詳細は`--detail`の指定行ごとに複数の文字列へ分かれるため、上限を文字列単位で適用すると
    1エントリの出力量が指定上限を超える。残り予算を出現順に配分して合計を上限内へ収める。
    """

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def clip(self, text: str) -> str:
        """残り予算の範囲で本文を制限し、消費した分を予算から差し引く。"""
        normalized = text.strip()
        if len(normalized) <= self.remaining:
            self.remaining -= len(normalized)
            return normalized
        room = self.remaining - len(_OMISSION_MARK)
        self.remaining = 0
        return normalized[:room] + _OMISSION_MARK if room > 0 else ""


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
    pending_question_lines: dict[str, int],
) -> dict[str, Any] | None:
    """対応するAskUserQuestionの結果だけを回答イベントへ変換する。

    質問と回答は別の行に由来するため、行番号には質問側（先頭行）の値を用いる。
    """
    if not isinstance(content, list):
        return None
    result_ids = {
        block["tool_use_id"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
    }
    matched_ids = set(pending_question_lines).intersection(result_ids)
    if not matched_ids:
        return None
    question_line = min(pending_question_lines.pop(matched_id) for matched_id in matched_ids)
    if not isinstance(result, dict):
        return None
    answers = result.get("answers")
    if not isinstance(answers, dict) or not all(
        isinstance(question, str) and isinstance(answer, str) for question, answer in answers.items()
    ):
        return None
    event = _question_answers_event([(question, [answer]) for question, answer in answers.items()])
    if event is not None:
        event["line"] = question_line
    return event


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


def _extract_claude(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """Claude Code形式を由来別の共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    pending_question_lines: dict[str, int] = {}
    for line, entry in zip(lines, entries, strict=True):
        for event in _claude_entry_events(entry, line, pending_question_lines):
            event.setdefault("line", line)
            events.append(event)
    return events


def _claude_entry_events(
    entry: dict[str, Any],
    line: int,
    pending_question_lines: dict[str, int],
) -> list[dict[str, Any]]:
    """Claude Codeの1エントリから共通イベントを取得する。"""
    events: list[dict[str, Any]] = []
    if entry.get("isSidechain") is True:
        completion = _completion_event(entry)
        if completion:
            events.append(completion)
        return events

    entry_type = entry.get("type")
    message = entry.get("message")
    user_texts: list[str] | None = None
    if isinstance(message, dict) and entry_type == "user" and message.get("role") == "user":
        user_texts = _text_blocks(message.get("content"))
        if any(STOP_ADVISOR_PREFIX in text for text in user_texts):
            return events
        skill_invocation = next((text for text in user_texts if text.startswith(_SKILL_INVOCATION_PREFIX)), None)
        if skill_invocation is not None:
            event = _event("skill-invocation", skill_invocation.splitlines()[0])
            if event:
                events.append(event)
            return events

    result = entry.get("toolUseResult")
    if isinstance(result, dict) and isinstance(result.get("stdout"), str) and SESSION_REVIEW_STARTED_MARKER in result["stdout"]:
        event = _event("session-review-started", SESSION_REVIEW_STARTED_MARKER)
        if event:
            events.append(event)

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
            for text in user_texts or ():
                event = _event("user", text)
                if event and not event["text"].startswith("<task-notification>"):
                    events.append(event)
            answer_event = _claude_answers_event(result, message.get("content"), pending_question_lines)
            if answer_event:
                events.append(answer_event)
            events.extend(_failed_tool_events(entry))
        elif entry_type == "assistant" and role == "assistant":
            pending_question_lines.update({call_id: line for call_id in _claude_question_call_ids(message.get("content"))})
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
    pending_questions: dict[str, tuple[int, dict[str, str]]],
) -> dict[str, Any] | None:
    """対応する回答outputの位置で、call_id内の既知質問だけをuserイベントへ変換する。

    質問と回答は別の行に由来するため、行番号には質問側（先頭行）の値を用いる。
    """
    call_id = payload.get("call_id")
    if not isinstance(call_id, str):
        return None
    pending = pending_questions.pop(call_id, None)
    output = _json_object(payload.get("output"))
    if pending is None or output is None:
        return None
    question_line, questions = pending
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
    if not pairs:
        return None
    event = _question_answers_event(pairs)
    if event is not None:
        event["line"] = question_line
    return event


def _codex_command_output(item: dict[str, Any]) -> str:
    """Codexコマンド実行項目から標準出力相当の本文を取得する。"""
    for key in ("aggregated_output", "output", "stdout"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_codex(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """Codex rollout形式を共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    pending_questions: dict[str, tuple[int, dict[str, str]]] = {}
    for line, entry in zip(lines, entries, strict=True):
        for event in _codex_entry_events(entry, line, pending_questions):
            event.setdefault("line", line)
            events.append(event)
    return events


def _codex_entry_events(
    entry: dict[str, Any],
    line: int,
    pending_questions: dict[str, tuple[int, dict[str, str]]],
) -> list[dict[str, Any]]:
    """Codexの1エントリから共通イベントを取得する。"""
    events: list[dict[str, Any]] = []
    entry_type = entry.get("type")
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return events
    payload_type = payload.get("type")
    if entry_type == "response_item" and payload_type == "function_call":
        question_call = _codex_question_call(payload)
        if question_call is not None:
            call_id, questions = question_call
            pending_questions[call_id] = (line, questions)
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
        event = _codex_command_event(payload)
        if event:
            events.append(event)
    return events


def _codex_command_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """完了したCodexコマンド実行から証拠となるイベントだけを取得する。"""
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "CommandExecution":
        return None
    status = item.get("status")
    output = _codex_command_output(item)
    if status == "completed" and SESSION_REVIEW_STARTED_MARKER in output:
        return _event("session-review-started", SESSION_REVIEW_STARTED_MARKER)
    if status != "failed":
        return None
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
    return event


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


def extract(entries: list[dict[str, Any]], lines: list[int] | None = None) -> list[dict[str, Any]]:
    """transcript形式を判定し、対象イベントを順序どおり抽出する。

    `lines`はエントリごとのtranscript行番号。省略時はエントリの並び順を行番号とみなす。
    """
    if not entries:
        return []
    runtime = _detect_runtime(entries)
    if runtime is None:
        return _fallback()
    return _finalize(_extract_for_runtime(entries, runtime, lines), runtime)


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


def _extract_for_runtime(
    entries: list[dict[str, Any]],
    runtime: _Runtime,
    lines: list[int] | None = None,
) -> list[dict[str, Any]]:
    """確定したruntimeに対応する共通イベントへ変換する。"""
    numbers = lines if lines is not None else list(range(1, len(entries) + 1))
    return _extract_codex(entries, numbers) if runtime == "codex" else _extract_claude(entries, numbers)


def _fallback() -> list[dict[str, Any]]:
    return [{"sequence": 1, "kind": "fallback", "text": _FALLBACK_TEXT}]


def _load_records(raw_path: str | None) -> list[_Record] | None:
    """絶対パスのJSONLを行番号付きで読み、失敗時は`None`を返す。"""
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    records: list[_Record] = []
    try:
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                records.append(_Record(number, line, parsed))
    except (json.JSONDecodeError, ValueError):
        return None
    return records


def load_and_extract(raw_path: str | None) -> list[dict[str, Any]]:
    """絶対パスのJSONLを一度読み、抽出結果またはfallbackを返す。"""
    records = _load_records(raw_path)
    if records is None:
        return _fallback()
    return _extract_records(records)


def _extract_records(records: list[_Record]) -> list[dict[str, Any]]:
    """読み込み済みレコードから行番号付きの時系列イベントを取得する。"""
    return extract([record.entry for record in records], [record.line for record in records])


def has_session_review_started(raw_path: str | None) -> bool:
    """対応するtranscriptに振り返りの手動起動または起動確定標識があれば真を返す。"""
    records = _load_records(raw_path)
    if records is None:
        return False
    entries = [record.entry for record in records]
    runtime = _detect_runtime(entries)
    if runtime is None:
        return False
    events = _extract_for_runtime(entries, runtime, [record.line for record in records])
    return any(
        event["kind"] == "session-review-started"
        or (event["kind"] == "user" and _is_manual_review_invocation(event["text"], runtime))
        for event in events
    )


def _warning_events(records: list[_Record]) -> list[dict[str, Any]]:
    """警告に一致した行を行番号付きで返す。一致なしはその事実を返す。

    走査対象を`--grep`と同じ本文（可視テキストとツール実行結果の生出力）へ揃える。
    エントリの生JSON行を対象にすると、識別子や構造キーへの一致で出力が肥大し、
    advisorの照会1回が出力退避を伴う規模になる。
    """
    events: list[dict[str, Any]] = []
    for record in records:
        matched_lines = [
            line_text
            for text in _entry_texts(record.entry)
            for line_text in text.splitlines()
            if _WARNING_PATTERN.search(line_text)
        ]
        if not matched_lines:
            continue
        hint = _tool_hint(record.entry)
        for line_text in matched_lines:
            event: dict[str, Any] = {"kind": "warning", "line": record.line, "text": _clip(line_text)}
            if hint:
                event["tool"] = hint
            events.append(event)
    return events or [{"kind": "warning", "text": "一致なし"}]


def _grep_events(records: list[_Record], pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    """可視テキストとツール実行結果の一致行と、一致したエントリ数の要約を返す。"""
    events: list[dict[str, Any]] = []
    matched = 0
    for record in records:
        hit = False
        for text in _entry_texts(record.entry):
            for line_text in text.splitlines():
                if pattern.search(line_text):
                    events.append({"kind": "match", "line": record.line, "text": _clip(line_text)})
                    hit = True
        matched += 1 if hit else 0
    events.append({"kind": "summary", "count": matched})
    return events


def _detail_events(records: list[_Record], numbers: list[int]) -> tuple[list[dict[str, Any]], int]:
    """指定行のエントリを整形して返す。範囲外の行番号はエラーと終了コード2を返す。"""
    index = {record.line: record.entry for record in records}
    events: list[dict[str, Any]] = []
    for number in numbers:
        entry = index.get(number)
        if entry is None:
            return [{"kind": "error", "text": f"行番号{number}は範囲外"}], 2
        events.extend(_entry_detail_events(number, entry))
    return events, 0


def _entry_detail_events(line: int, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """1エントリの詳細を、tool_use・tool_resultのブロック単位で整形する。

    クリップの上限はエントリ全体で共有し、ブロックの出現順に予算を配分する。
    """
    budget = _DetailBudget(_MAX_DETAIL_LENGTH)
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    events: list[dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                events.append(
                    {
                        "kind": "detail",
                        "line": line,
                        "name": str(block.get("name", "")),
                        "input": _clip_structure(block.get("input"), budget),
                    }
                )
            elif block.get("type") == "tool_result":
                body = "\n".join(_text_blocks(block.get("content")))
                events.append(
                    {
                        "kind": "detail",
                        "line": line,
                        "tool": str(block.get("tool_use_id", "")),
                        "text": budget.clip(body),
                    }
                )
    if events:
        return events
    return [{"kind": "detail", "line": line, "text": budget.clip(json.dumps(entry, ensure_ascii=False, indent=2))}]


def _clip_structure(value: Any, budget: _DetailBudget) -> Any:
    """入力の構造を保ったまま、文字列だけをエントリ共有の予算で制限する。"""
    if isinstance(value, str):
        return budget.clip(value)
    if isinstance(value, dict):
        return {key: _clip_structure(item, budget) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_structure(item, budget) for item in value]
    return value


def _tool_use_result_texts(entry: dict[str, Any], visible_texts: list[str]) -> list[str]:
    """ツール実行結果の生出力のうち、可視テキストに現れない本文だけを取得する。

    大きなBash出力は本文が外部ファイルへ退避され、可視テキストには退避通知だけが残る。
    退避された本文は`toolUseResult`側にしか無いため、検索対象から欠落させない。
    通常の出力は可視テキストと同一になるため、一致行の重複を防ぐ目的で除外する。
    """
    result = entry.get("toolUseResult")
    if not isinstance(result, dict):
        return []
    visible = "\n".join(visible_texts)
    return [
        value
        for key in ("stdout", "stderr")
        if isinstance(value := result.get(key), str) and value.strip() and value.strip() not in visible
    ]


def _entry_texts(entry: dict[str, Any]) -> list[str]:
    """runtimeを問わず、1エントリの検索対象テキストを取得する。

    Claude Codeではtool_result本文・tool_use入力とツール実行結果の生出力、Codexではメッセージ本文と
    コマンド出力・関数呼び出しのJSON本文を対象とする。
    """
    message = entry.get("message")
    if isinstance(message, dict) or isinstance(entry.get("toolUseResult"), dict):
        content = message.get("content") if isinstance(message, dict) else None
        texts = _text_blocks(content, include_tool_results=True)
        if isinstance(content, list):
            texts.extend(
                json.dumps(block.get("input") or {}, ensure_ascii=False)
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
        texts.extend(_tool_use_result_texts(entry, texts))
        return [text for text in texts if text]

    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return []
    texts = _codex_text_blocks(payload.get("content"))
    texts.extend(value for key in ("message", "text", "arguments", "output") if isinstance(value := payload.get(key), str))
    item = payload.get("item")
    if isinstance(item, dict):
        texts.append(_codex_command_output(item))
        texts.append(json.dumps(item.get("command"), ensure_ascii=False) if item.get("command") else "")
    return [text for text in texts if text]


def _tool_hint(entry: dict[str, Any]) -> str | None:
    """エントリに含まれるコマンド先頭行またはtool_use_idを取得する。"""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str) and command.strip():
                    return _clip(command.splitlines()[0])
                if isinstance(block.get("id"), str):
                    return block["id"]
            if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                return block["tool_use_id"]

    payload = entry.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    command = item.get("command") if isinstance(item, dict) else None
    if isinstance(command, list) and command:
        return _clip(" ".join(part for part in command if isinstance(part, str)).splitlines()[0])
    return None


def _print_events(events: list[dict[str, Any]]) -> None:
    """イベント列を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    for event in events:
        print(json.dumps(event, ensure_ascii=False))


def _print_error(text: str) -> int:
    """照会不能を示すエラーを出力し、終了コード2を返す。"""
    _print_events([{"kind": "error", "text": text}])
    return 2


def _build_parser() -> argparse.ArgumentParser:
    """既定の抽出と照会モードの引数を定義する。"""
    parser = argparse.ArgumentParser(description="transcriptから振り返り用の時系列証拠を抽出・照会する。")
    parser.add_argument(
        "transcript_path",
        nargs="?",
        help="transcriptの絶対パス。省略時と読み込み失敗時はfallback指示を出力する。",
    )
    parser.add_argument(
        "--warn",
        action="store_true",
        help="本文（可視テキストとツール実行結果）のうち警告（`警告`・`warn`、大小文字を区別しない）に一致した行を照会する。",
    )
    parser.add_argument(
        "--grep",
        metavar="REGEX",
        help="本文（可視テキストとツール実行結果）を正規表現で検索し、一致行と一致エントリ数を照会する。",
    )
    parser.add_argument(
        "--detail",
        metavar="LINE",
        nargs="+",
        type=int,
        help="指定した行番号のエントリの詳細（tool_useの入力全体・tool_result本文）を照会する。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """証拠または照会結果を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if sum((args.warn, args.grep is not None, args.detail is not None)) > 1:
        return _print_error("--warn・--grep・--detailは併用できない")

    records = _load_records(args.transcript_path)
    if records is None:
        _print_events(_fallback())
        return 0

    if args.warn:
        _print_events(_warning_events(records))
        return 0
    if args.grep is not None:
        try:
            pattern = re.compile(args.grep)
        except re.error as error:
            return _print_error(f"正規表現が不正: {error}")
        _print_events(_grep_events(records, pattern))
        return 0
    if args.detail is not None:
        events, exit_code = _detail_events(records, args.detail)
        _print_events(events)
        return exit_code

    _print_events(_extract_records(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
