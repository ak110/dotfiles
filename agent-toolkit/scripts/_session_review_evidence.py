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
import datetime
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Literal, NamedTuple

_MAX_TEXT_LENGTH = 2000
_MAX_DETAIL_LENGTH = 8000
_OMISSION_MARK = "…[省略]"
_WARNING_PATTERN = re.compile("警告|warn", re.IGNORECASE)
_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+\t(.*)$")
_SKILL_INVOCATION_PREFIX = "Base directory for this skill: "
_SELF_SCRIPT_STEM = "_session_review_evidence"
_SEGMENT_SEPARATORS = re.compile(r"[;&|]+")
_PATH_SEPARATORS = re.compile(r"[/\\]")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_NAMES = frozenset({"sh", "bash", "zsh"})
_SCRIPT_RUNNERS = frozenset({"uv", "uvx", "env"})
_PERSISTED_OUTPUT_PREFIX = "<persisted-output>"
STOP_ADVISOR_PREFIX = "[auto-generated: agent-toolkit/stop_advisor]"
SESSION_REVIEW_STARTED_MARKER = "[auto-generated: agent-toolkit/session-review-started]"
_FALLBACK_TEXT = (
    "transcript_pathを読み取れないため抽出証拠を生成できない。"
    "継承した会話履歴を評価し、取得できない範囲を未検証と明記すること。"
)
_METADATA_KEYS = frozenset(
    {
        # 識別子・署名
        "uuid",
        "parentUuid",
        "leafUuid",
        "sessionId",
        "session_id",
        "bridgeSessionId",
        "requestId",
        "promptId",
        "messageId",
        "id",
        "call_id",
        "tool_use_id",
        "toolUseID",
        "sourceToolUseID",
        "sourceToolAssistantUUID",
        "agentId",
        "taskId",
        "ownerAccountUuid",
        "ownerOrganizationUuid",
        "signature",
        # 時刻
        "timestamp",
        "backupTime",
        # 形式・区分の名称
        "type",
        "subtype",
        "kind",
        "role",
        "status",
        "stop_reason",
        "stopReason",
        "sessionKind",
        "hookEvent",
        "hookEventName",
        "permissionMode",
        "mode",
        "userType",
        "entrypoint",
        "promptSource",
        # 実行環境・モデル設定
        "version",
        "model",
        "resolvedModel",
        "effort",
        "service_tier",
        "inference_geo",
        "speed",
        "cwd",
        "originalCwd",
        "preEnterOriginalCwd",
        "gitBranch",
        "originalBranch",
        "originalHeadCommit",
        "worktreePath",
        "worktreeName",
        "worktreeBranch",
    }
)
_BODY_KEYS = frozenset(
    {
        # 自由形式の本文を保持するフィールド。内部のキー名は利用者の入力に由来する
        "input",
        "output",
        "arguments",
        "prompt",
    }
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
    """1エントリの詳細出力が共有する残り文字数と、省略の発生有無。

    詳細は`--detail`の指定行ごとに複数の文字列へ分かれるため、上限を文字列単位で適用すると
    1エントリの出力量が指定上限を超える。残り予算を出現順に配分して本文の合計を上限内へ収める。
    省略標識も返す文字数として予算から差し引くため、文字列値の個数が増えても合計は上限を超えない。
    予算が標識の長さに満たない時点以降の本文は空文字列となり、本文が元から空である場合と
    文字列単体では区別できない。この区別のため、省略が1回でも生じたかを`omitted`が保持し、
    呼び出し側が当該エントリのイベントへ標識として付ける。
    """

    def __init__(self, limit: int) -> None:
        self.remaining = limit
        self.omitted = False

    def clip(self, text: str) -> str:
        """残り予算の範囲で本文を制限し、返した文字数を予算から差し引く。

        予算を超える非空の本文は、省略標識を含めて残り予算へ収まる範囲まで切り詰める。
        省略標識を置く余地も無い場合は空文字列を返す。いずれの場合も省略の発生を記録する。
        """
        normalized = text.strip()
        if len(normalized) <= self.remaining:
            self.remaining -= len(normalized)
            return normalized
        self.omitted = True
        if self.remaining < len(_OMISSION_MARK):
            return ""
        clipped = normalized[: self.remaining - len(_OMISSION_MARK)] + _OMISSION_MARK
        self.remaining -= len(clipped)
        return clipped


def _text_blocks(content: Any) -> list[str]:
    """Message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]


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
    boundary = _review_boundary_index(events, runtime)
    events = [event for event in events[:boundary] if event["kind"] != "session-review-started"]

    for event in reversed(events):
        if event["kind"] == "assistant":
            event["kind"] = "final-result"
            break
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def _review_boundary_index(events: list[dict[str, Any]], runtime: _Runtime) -> int:
    """既存の抽出契約に従う振り返り境界のイベント位置を返す。"""
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
    return boundary


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


_CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_CODEX_TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_THREAD_ID_KEYS = ("threadId", "conversationId")
_CODEX_TOOL_NAMES = frozenset({"mcp__codex__codex", "mcp__codex__codex-reply"})
_TASK_RESULT_PATTERN = re.compile(r"<task-notification\b[^>]*>.*?<result>\s*(.*?)\s*</result>", re.DOTALL)


def _record_timestamp(record: _Record) -> datetime.datetime | None:
    value = record.entry.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _token_value(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _claude_tokens(usage: dict[str, Any]) -> dict[str, int]:
    return {key: _token_value(usage.get(key)) for key in _CLAUDE_TOKEN_KEYS}


def _token_total(tokens: dict[str, int]) -> int:
    return sum(value for value in tokens.values())


def _add_tokens(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


def _latest_claude_usages(records: list[_Record]) -> list[tuple[_Record, dict[str, int]]]:
    """同一`message.id`の重複エントリを最後のusageだけへ畳み込む。

    Claude Code transcriptでは同一`message.id`のエントリが複数現れ、各エントリのusageを合算すると
    トークン消費量が数倍になる。実測した重複形状に合わせ、最後に現れたusageを採用する。
    """
    latest: dict[str, tuple[_Record, dict[str, int]]] = {}
    for record in records:
        message = record.entry.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        message_id = message.get("id")
        key = f"message:{message_id}" if isinstance(message_id, str) else f"line:{record.line}"
        latest[key] = (record, _claude_tokens(usage))
    return list(latest.values())


def _codex_token_usages(records: list[_Record]) -> list[tuple[_Record, dict[str, int], int]]:
    usages: list[tuple[_Record, dict[str, int], int]] = []
    for record in records:
        payload = record.entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        total_usage = info.get("total_token_usage")
        last_usage = info.get("last_token_usage")
        if not isinstance(total_usage, dict):
            continue
        tokens = {key: _token_value(total_usage.get(key)) for key in _CODEX_TOKEN_KEYS}
        context = _token_value(last_usage.get("input_tokens")) if isinstance(last_usage, dict) else 0
        usages.append((record, tokens, context))
    return usages


def _stats_summary_data(records: list[_Record], runtime: _Runtime) -> dict[str, Any]:
    timestamps = [(record, timestamp) for record in records if (timestamp := _record_timestamp(record)) is not None]
    summary: dict[str, Any] = {}
    if timestamps:
        first_record, first_timestamp = min(timestamps, key=lambda item: item[1])
        last_record, last_timestamp = max(timestamps, key=lambda item: item[1])
        summary["start"] = first_record.entry["timestamp"]
        summary["end"] = last_record.entry["timestamp"]
        summary["elapsed_seconds"] = int((last_timestamp - first_timestamp).total_seconds())

    if runtime == "claude":
        usages = _latest_claude_usages(records)
        if not usages:
            return summary
        tokens: dict[str, int] = {key: 0 for key in _CLAUDE_TOKEN_KEYS}
        max_context = 0
        for _, usage in usages:
            _add_tokens(tokens, usage)
            max_context = max(
                max_context,
                usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"] + usage["input_tokens"],
            )
        summary.update(tokens=tokens, max_context_tokens=max_context, api_messages=len(usages))
        return summary

    usages = _codex_token_usages(records)
    if not usages:
        return summary
    tokens = dict(usages[-1][1])
    summary.update(
        tokens=tokens,
        max_context_tokens=max(context for _, _, context in usages),
        api_messages=len(usages),
    )
    return summary


def _stats_boundary_line(records: list[_Record], runtime: _Runtime) -> int | None:
    events = _extract_for_runtime([record.entry for record in records], runtime, [record.line for record in records])
    boundary = _review_boundary_index(events, runtime)
    if boundary >= len(events):
        return None
    line = events[boundary].get("line")
    return line if isinstance(line, int) else None


def _stats_main_records(records: list[_Record], runtime: _Runtime) -> tuple[list[_Record], datetime.datetime | None]:
    boundary_line = _stats_boundary_line(records, runtime)
    if boundary_line is None:
        return records, None
    boundary_record = next((record for record in records if record.line == boundary_line), None)
    return [record for record in records if record.line < boundary_line], (
        _record_timestamp(boundary_record) if boundary_record is not None else None
    )


def _stats_subagent_records(
    transcript_path: str,
    boundary_timestamp: datetime.datetime | None,
) -> tuple[list[tuple[str, str | None, list[_Record]]], int]:
    path = Path(transcript_path)
    subagent_dir = path.with_suffix("") / "subagents"
    try:
        paths = sorted(subagent_dir.glob("agent-*.jsonl"))
    except OSError:
        return [], 0
    metadata: dict[str, tuple[str | None, str | None]] = {}
    for record_path in paths:
        agent_id = record_path.stem
        meta_path = record_path.with_name(f"{agent_id}.meta.json")
        try:
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            raw_meta = {}
        if not isinstance(raw_meta, dict):
            raw_meta = {}
        agent_type = raw_meta.get("agentType") if isinstance(raw_meta.get("agentType"), str) else None
        parent = raw_meta.get("parentAgentId") if isinstance(raw_meta.get("parentAgentId"), str) else None
        metadata[agent_id] = (agent_type, parent)

    excluded: set[str] = {
        agent_id for agent_id, (agent_type, _) in metadata.items() if agent_type and "session-review-advisor" in agent_type
    }
    changed = True
    while changed:
        changed = False
        for agent_id, (_, parent) in metadata.items():
            if agent_id not in excluded and parent in excluded:
                excluded.add(agent_id)
                changed = True

    selected: list[tuple[str, str | None, list[_Record]]] = []
    excluded_count = 0
    for record_path in paths:
        agent_id = record_path.stem
        records = _load_records(str(record_path))
        if records is None:
            continue
        if boundary_timestamp is not None:
            starts = [_record_timestamp(record) for record in records]
            start = min((value for value in starts if value is not None), default=None)
            if start is None or start >= boundary_timestamp:
                continue
        if agent_id in excluded:
            excluded_count += 1
            continue
        selected.append((agent_id, metadata.get(agent_id, (None, None))[0], records))
    return selected, excluded_count


def _thread_id_from_mapping(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in _THREAD_ID_KEYS:
        thread = value.get(key)
        if isinstance(thread, str) and thread:
            return thread
    return None


def _thread_ids_from_record(record: _Record) -> list[str]:
    entry = record.entry
    thread_ids: list[str] = []
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") not in _CODEX_TOOL_NAMES:
                continue
            thread = _thread_id_from_mapping(block.get("input"))
            if thread:
                thread_ids.append(thread)

    mcp_meta = entry.get("mcpMeta")
    if isinstance(mcp_meta, dict):
        thread = _thread_id_from_mapping(mcp_meta.get("structuredContent"))
        if thread:
            thread_ids.append(thread)

    tool_result = entry.get("toolUseResult")
    if isinstance(tool_result, dict):
        thread = _thread_id_from_mapping(tool_result)
        if thread:
            thread_ids.append(thread)
    elif isinstance(tool_result, str):
        thread = _thread_id_from_mapping(_json_object(tool_result))
        if thread:
            thread_ids.append(thread)

    notification_texts: list[str] = []
    if entry.get("type") == "queue-operation":
        notification_texts.extend(_text_blocks(entry.get("content")))
    notification_texts.extend(_text_blocks(content))
    for text in notification_texts:
        for match in _TASK_RESULT_PATTERN.finditer(text):
            result = _json_object(match.group(1))
            thread = _thread_id_from_mapping(result)
            if thread:
                thread_ids.append(thread)
    return list(dict.fromkeys(thread_ids))


def _rollout_path(thread_id: str) -> Path | None:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    escaped_thread = re.escape(thread_id)
    candidates = sorted(
        path
        for path in codex_home.glob(f"sessions/*/*/*/rollout-*{thread_id}.jsonl")
        if re.search(rf"rollout-.*{escaped_thread}\.jsonl$", path.name)
    )
    return candidates[0] if candidates else None


def _stats_thread_records(
    main_records: list[_Record],
    subagents: list[tuple[str, str | None, list[_Record]]],
) -> dict[str, tuple[int, str | None]]:
    threads: dict[str, tuple[int, str | None]] = {}
    for record in main_records:
        for thread_id in _thread_ids_from_record(record):
            threads.setdefault(thread_id, (record.line, None))
    for agent_id, _, records in subagents:
        for record in records:
            for thread_id in _thread_ids_from_record(record):
                threads.setdefault(thread_id, (record.line, agent_id))
    return threads


def _stats_call_entries(records: list[_Record], runtime: _Runtime) -> list[dict[str, Any]]:
    calls: dict[str, tuple[str, str | None, int, datetime.datetime]] = {}
    results: dict[str, list[datetime.datetime]] = {}
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        if runtime == "claude":
            message = record.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_use" and isinstance(block.get("id"), str):
                    block_input = block.get("input")
                    command = block_input.get("command") if isinstance(block_input, dict) else None
                    hint = _clip(command.splitlines()[0]) if isinstance(command, str) and command.strip() else None
                    calls.setdefault(block["id"], (str(block.get("name", "")), hint, record.line, timestamp))
                elif block_type == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    results.setdefault(block["tool_use_id"], []).append(timestamp)
            continue

        payload = record.entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if payload_type in {"custom_tool_call", "function_call"} and isinstance(payload.get("call_id"), str):
            arguments = _json_object(payload.get("arguments"))
            command = arguments.get("command") if isinstance(arguments, dict) else None
            if isinstance(command, list):
                hint = _clip(" ".join(part for part in command if isinstance(part, str)).splitlines()[0]) if command else None
            elif isinstance(command, str) and command.strip():
                hint = _clip(command.splitlines()[0])
            else:
                hint = None
            calls.setdefault(payload["call_id"], (str(payload.get("name", "")), hint, record.line, timestamp))
        elif payload_type in {"custom_tool_call_output", "function_call_output"} and isinstance(payload.get("call_id"), str):
            results.setdefault(payload["call_id"], []).append(timestamp)

    paired: list[dict[str, Any]] = []
    for call_id, (name, hint, line, started) in calls.items():
        finished = next((value for value in results.get(call_id, []) if value >= started), None)
        if finished is None:
            continue
        item: dict[str, Any] = {
            "tool": name,
            "seconds": (finished - started).total_seconds(),
            "line": line,
        }
        if hint:
            item["hint"] = hint
        paired.append(item)
    return paired


def _stats_token_peaks(records: list[_Record], runtime: _Runtime) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if runtime == "claude":
        usages = _latest_claude_usages(records)
        for record, tokens in usages:
            candidates.append(
                {
                    "total_tokens": _token_total(tokens),
                    **tokens,
                    "line": record.line,
                    "new_tokens": tokens["output_tokens"] + tokens["cache_creation_input_tokens"],
                }
            )
    else:
        for record, _, _ in _codex_token_usages(records):
            payload = record.entry.get("payload")
            info = payload.get("info") if isinstance(payload, dict) else None
            usage = info.get("last_token_usage") if isinstance(info, dict) else None
            if not isinstance(usage, dict):
                continue
            input_tokens = _token_value(usage.get("input_tokens"))
            output_tokens = _token_value(usage.get("output_tokens"))
            candidates.append(
                {
                    "total_tokens": input_tokens + output_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "input_tokens": input_tokens,
                    "line": record.line,
                    "new_tokens": output_tokens,
                }
            )
    by_total = sorted(candidates, key=lambda item: (-item["total_tokens"], item["line"]))[:10]
    by_new = sorted(candidates, key=lambda item: (-item["new_tokens"], item["line"]))[:10]
    selected = {item["line"]: item for item in by_total}
    selected.update({item["line"]: item for item in by_new})
    return [
        {key: value for key, value in item.items() if key != "new_tokens"}
        for item in sorted(selected.values(), key=lambda value: (-value["total_tokens"], value["line"]))
    ]


def _stats_events(records: list[_Record], runtime: _Runtime, transcript_path: str) -> list[dict[str, Any]]:
    main_records, boundary_timestamp = _stats_main_records(records, runtime)
    summary = _stats_summary_data(main_records, runtime)
    subagents: list[tuple[str, str | None, list[_Record]]] = []
    excluded_review_agents = 0
    if runtime == "claude":
        subagents, excluded_review_agents = _stats_subagent_records(transcript_path, boundary_timestamp)
    threads = _stats_thread_records(main_records, subagents)
    thread_summaries: list[tuple[str, dict[str, Any], int, str | None]] = []
    for thread_id, (line, agent_id) in threads.items():
        rollout = _rollout_path(thread_id)
        if rollout is None:
            continue
        rollout_records = _load_records(str(rollout))
        if rollout_records is None:
            continue
        thread_summary = _stats_summary_data(rollout_records, "codex")
        thread_summaries.append((thread_id, thread_summary, line, agent_id))

    total_tokens: dict[str, int] = {}
    if isinstance(summary.get("tokens"), dict):
        _add_tokens(total_tokens, summary["tokens"])
    for _, _, subagent_records in subagents:
        sub_summary = _stats_summary_data(subagent_records, "claude")
        if isinstance(sub_summary.get("tokens"), dict):
            _add_tokens(total_tokens, sub_summary["tokens"])
    for _, thread_summary, _, _ in thread_summaries:
        if isinstance(thread_summary.get("tokens"), dict):
            _add_tokens(total_tokens, thread_summary["tokens"])

    total_event: dict[str, Any] = {
        "kind": "stats-total",
        "tokens": total_tokens,
        "subagent_count": len(subagents),
        "codex_thread_count": len(thread_summaries),
    }
    if "elapsed_seconds" in summary:
        total_event["elapsed_seconds"] = summary["elapsed_seconds"]
    events = [total_event]
    events.append({"kind": "stats-summary", **summary} if summary else {"kind": "stats-summary", "text": "集計対象なし"})

    timestamped_records = [
        (record, timestamp) for record in main_records if (timestamp := _record_timestamp(record)) is not None
    ]
    gaps = sorted(
        (
            (after_timestamp - before_timestamp).total_seconds(),
            before.line,
            after.line,
        )
        for (before, before_timestamp), (after, after_timestamp) in zip(
            timestamped_records, timestamped_records[1:], strict=False
        )
        if (after_timestamp - before_timestamp).total_seconds() >= 60
    )
    events.extend(
        {"kind": "stats-gap", "seconds": round(seconds, 1), "before_line": before, "after_line": after}
        for seconds, before, after in sorted(gaps, reverse=True)[:10]
    )

    calls = _stats_call_entries(main_records, runtime)
    tool_groups: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        tool_groups.setdefault(call["tool"], []).append(call)
    events.extend(
        {
            "kind": "stats-tool",
            "tool": tool,
            "count": len(items),
            "total_seconds": round(sum(item["seconds"] for item in items), 1),
        }
        for tool, items in sorted(tool_groups.items(), key=lambda item: (-sum(call["seconds"] for call in item[1]), item[0]))[
            :20
        ]
    )
    repeats: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for call in calls:
        repeats.setdefault((call["tool"], call.get("hint")), []).append(call)
    for (tool, hint), items in sorted(repeats.items(), key=lambda item: (-len(item[1]), item[0][0])):
        if len(items) < 2:
            continue
        event: dict[str, Any] = {
            "kind": "stats-repeat",
            "tool": tool,
            "count": len(items),
            "lines": [item["line"] for item in items],
        }
        if hint:
            event["hint"] = hint
        events.append(event)
    for call in sorted(calls, key=lambda item: (-item["seconds"], item["line"]))[:10]:
        event = {"kind": "stats-slow-call", "tool": call["tool"], "seconds": round(call["seconds"], 1), "line": call["line"]}
        if call.get("hint"):
            event["hint"] = call["hint"]
        events.append(event)
    events.extend({"kind": "stats-token-peak", **peak} for peak in _stats_token_peaks(main_records, runtime))

    if subagents:
        subagent_rows: list[tuple[str, str | None, dict[str, Any]]] = []
        subagent_total: dict[str, int] = {}
        for agent_id, agent_type, subagent_records in subagents:
            sub_summary = _stats_summary_data(subagent_records, "claude")
            row: dict[str, Any] = {"agent": agent_id, **sub_summary}
            if agent_type:
                row["agent_type"] = agent_type
            row["elapsed_seconds"] = sub_summary.get("elapsed_seconds", 0)
            row["tokens"] = sub_summary.get("tokens", {key: 0 for key in _CLAUDE_TOKEN_KEYS})
            row["api_messages"] = sub_summary.get("api_messages", 0)
            subagent_rows.append((agent_id, agent_type, row))
            _add_tokens(subagent_total, row["tokens"])
        for _, _, row in sorted(subagent_rows, key=lambda item: (-_token_total(item[2]["tokens"]), item[0])):
            events.append({"kind": "stats-subagent", **row})
        events.append(
            {
                "kind": "stats-subagent-total",
                "count": len(subagents),
                "tokens": subagent_total,
                "excluded_review_agents": excluded_review_agents,
            }
        )

    for thread_id, thread_summary, line, agent_id in sorted(
        thread_summaries,
        key=lambda item: (-_token_total(item[1].get("tokens", {})), item[0]),
    ):
        thread_event: dict[str, Any] = {
            "kind": "stats-codex-thread",
            "thread": thread_id,
            **thread_summary,
            "line": line,
        }
        if agent_id:
            thread_event["agent"] = agent_id
        events.append(thread_event)
    return events


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

    走査対象は`--grep`と同じく`_entry_texts`が集めるエントリ内の全本文とする。
    エントリの生JSON行を対象にすると、識別子や構造キーへの一致で出力が肥大し、
    advisorの照会1回が出力退避を伴う規模になる。
    """
    events: list[dict[str, Any]] = []
    for record in _scannable_records(records):
        matched_lines = _matched_lines(record.entry, _WARNING_PATTERN)
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
    """エントリ内の全本文から一致行を集め、一致したエントリ数の要約を末尾へ付ける。"""
    events: list[dict[str, Any]] = []
    matched = 0
    for record in _scannable_records(records):
        matched_lines = _matched_lines(record.entry, pattern)
        events.extend({"kind": "match", "line": record.line, "text": _clip(line_text)} for line_text in matched_lines)
        matched += 1 if matched_lines else 0
    events.append({"kind": "summary", "count": matched})
    return events


def _scannable_records(records: list[_Record]) -> list[_Record]:
    """照会の走査対象から、本スクリプト自身の実行記録を除いたレコードを返す。

    本スクリプトを呼び出したコマンドの記録には、`--warn`のフラグ文字列や過去の照会結果本文が
    そのまま残る。これらは検索語へ機械的に一致し、実在しない警告・一致として報告される。
    除外対象は自己呼び出しの記録と、対応する実行結果の記録だけとし、
    本文が同じ文字列を含むだけの無関係な記録は走査対象に残す。
    """
    self_call_ids: set[str] = set()
    scannable: list[_Record] = []
    for record in records:
        call_ids = _self_invocation_call_ids(record.entry)
        if call_ids is not None:
            self_call_ids |= call_ids
            continue
        if _result_call_ids(record.entry) & self_call_ids:
            continue
        scannable.append(record)
    return scannable


def _self_invocation_call_ids(entry: dict[str, Any]) -> set[str] | None:
    """本スクリプト自身を呼び出した記録なら呼び出しIDの集合を、そうでなければ`None`を返す。

    実行結果を呼び出しと同じ記録へ含む形式では対応付けが不要なため、空集合を返す場合がある。
    判定対象は実行されたコマンドに限り、スクリプトを検索・閲覧・編集する操作は自己呼び出しとみなさない。
    """
    call_ids: set[str] = set()
    invoked = False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            block_input = block.get("input")
            command = block_input.get("command") if isinstance(block_input, dict) else None
            if not isinstance(command, str) or not _runs_self_script(_shell_tokens(command)):
                continue
            invoked = True
            if isinstance(block.get("id"), str):
                call_ids.add(block["id"])

    payload = entry.get("payload")
    if isinstance(payload, dict) and _runs_self_script(_payload_command_tokens(payload)):
        invoked = True
        if isinstance(payload.get("call_id"), str):
            call_ids.add(payload["call_id"])
    return call_ids if invoked else None


def _runs_self_script(tokens: list[str]) -> bool:
    """トークン列のいずれかのコマンドが本スクリプトを実行しているかを判定する。

    スクリプト名が現れるだけでは実行と扱わない。検索・閲覧・編集コマンドの引数として
    ファイル名を渡す操作を実行と誤認すると、その実行結果に含まれる実在の警告を照会できなくなる。
    """
    if not any(_SELF_SCRIPT_STEM in token for token in tokens):
        return False
    return any(_command_runs_self(segment) for segment in _command_segments(tokens))


def _command_segments(tokens: list[str]) -> list[list[str]]:
    """区切り記号（`;`・`&&`・`|`など）でトークン列を個々のコマンドへ分ける。"""
    segments: list[list[str]] = [[]]
    for token in tokens:
        for index, part in enumerate(_SEGMENT_SEPARATORS.split(token)):
            if index:
                segments.append([])
            if part:
                segments[-1].append(part)
    return [segment for segment in segments if segment]


def _command_runs_self(tokens: list[str]) -> bool:
    """1つのコマンドの実行形式と引数の並びから、本スクリプトの実行かどうかを判定する。

    先頭語がスクリプト自身なら直接起動、インタープリターなら引数の位置での起動と扱う。
    シェルへコマンド文字列を渡す形式では、その文字列を1つのコマンドとして再帰的に判定する。
    """
    index = 0
    while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
        index += 1
    words = tokens[index:]
    if not words:
        return False
    if _is_self_script(words[0]):
        return True
    name = _basename(words[0])
    if name in _SHELL_NAMES:
        return any(_runs_self_script(_shell_tokens(word)) for word in words[1:] if not word.startswith("-"))
    if name in _SCRIPT_RUNNERS or name.startswith("python"):
        return any(_is_self_script(word) for word in words[1:])
    return False


def _is_self_script(token: str) -> bool:
    """トークンが本スクリプトのファイルを指しているかを返す。"""
    return _basename(token).startswith(_SELF_SCRIPT_STEM)


def _basename(token: str) -> str:
    """パス区切りを除いたトークン末尾の名前を返す。"""
    return _PATH_SEPARATORS.split(token)[-1]


def _shell_tokens(command: str) -> list[str]:
    """コマンド文字列をシェルの引用規則で分解する。分解できない場合は空白で分ける。"""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _payload_command_tokens(payload: dict[str, Any]) -> list[str]:
    """Codexの記録から、実行したコマンドのトークン列を取得する。

    コマンドを保持するキー名は記録の種類で異なり、コマンド実行の呼び出し引数は`cmd`、
    完了項目は`command`を用いる。いずれの記録も同じ判定へ渡すため、両方のキーを探す。
    """
    item = payload.get("item")
    for source in (_json_object(payload.get("arguments")), item if isinstance(item, dict) else None):
        if source is None:
            continue
        for key in ("command", "cmd"):
            command = source.get(key)
            if isinstance(command, list):
                return [part for part in command if isinstance(part, str)]
            if isinstance(command, str):
                return _shell_tokens(command)
    return []


def _result_call_ids(entry: dict[str, Any]) -> set[str]:
    """エントリが返しているツール実行結果の呼び出しIDを取得する。"""
    call_ids: set[str] = set()
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        call_ids |= {
            block["tool_use_id"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
        }

    payload = entry.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "function_call_output" and isinstance(payload.get("call_id"), str):
        call_ids.add(payload["call_id"])
    return call_ids


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
    予算超過で省略が生じたエントリは、当該エントリの全イベントへ`omitted`を付ける。
    予算が尽きた後の本文は空文字列となるため、この標識が無ければ
    空の出力が元から空だったのか省略の結果なのかを判別できない。
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
                events.append(
                    {
                        "kind": "detail",
                        "line": line,
                        "tool": str(block.get("tool_use_id", "")),
                        "text": budget.clip(_tool_result_body(block, entry)),
                    }
                )
    if not events:
        events = [{"kind": "detail", "line": line, "text": budget.clip(json.dumps(entry, ensure_ascii=False, indent=2))}]
    if budget.omitted:
        for event in events:
            event["omitted"] = True
    return events


def _tool_result_body(block: dict[str, Any], entry: dict[str, Any]) -> str:
    """tool_resultブロックの実体本文を取得する。

    大きなツール出力は退避先ファイルへ移され、message側のcontentには退避通知だけが残る。
    この形態ではmessage側から実際の出力を取得できないため、
    同エントリのツール実行結果が持つ標準出力・標準エラーを本文とする。
    """
    body = "\n".join(_text_blocks(block.get("content")))
    if body.strip() and not body.lstrip().startswith(_PERSISTED_OUTPUT_PREFIX):
        return body
    result = entry.get("toolUseResult")
    if isinstance(result, str):
        return result or body
    if not isinstance(result, dict):
        return body
    streams = [value for key in ("stdout", "stderr") if isinstance(value := result.get(key), str) and value.strip()]
    return "\n".join(streams) or body


def _clip_structure(value: Any, budget: _DetailBudget) -> Any:
    """入力の構造を保ったまま、文字列だけをエントリ共有の予算で制限する。"""
    if isinstance(value, str):
        return budget.clip(value)
    if isinstance(value, dict):
        return {key: _clip_structure(item, budget) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_structure(item, budget) for item in value]
    return value


def _entry_texts(entry: dict[str, Any]) -> list[str]:
    """runtimeを問わず、1エントリの検索対象テキストを出現順に取得する。

    エントリの構造を再帰的にたどり、文字列値をすべて集める。
    メッセージ本文・tool_use入力・tool_result本文・ツール実行結果の生出力に加え、
    hook通知が入る`attachment`配下のような未知のフィールドも対象となる。
    既知フィールドを列挙する方式は、通知の格納先が増えるたびに検索対象から漏れるため採らない。
    `_METADATA_KEYS`の値は本文を持たない管理用の値（識別子・時刻・形式名・実行環境）であり、
    走査しても一致を増やすだけとなるため除外する。
    除外の可否は深さではなく、値を保持するフィールドの構造上の役割で判定する。
    エントリ・`message`・`payload`・`item`・各ブロックのようなプロトコル構造は、
    深さを問わず区分値と識別子を保持するため除外の対象とする。
    `input`・`output`・`arguments`・`prompt`のような自由形式の本文フィールドでは、
    `mode`・`status`のような汎用語のキーが利用者の入力そのものを保持するため、
    その内部のキーを除外しない。
    """
    texts: list[str] = []
    _collect_texts(entry, texts)
    return texts


def _collect_texts(value: Any, texts: list[str], *, in_body: bool = False) -> None:
    """構造をたどり、プロトコル構造が持つ管理用フィールドを除く文字列値を`texts`へ追加する。

    `in_body`は、自由形式の本文を保持するフィールドの内部を走査中であることを表す。
    """
    if isinstance(value, str):
        if value.strip():
            texts.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not in_body and key in _METADATA_KEYS:
                continue
            _collect_texts(item, texts, in_body=in_body or key in _BODY_KEYS)
    elif isinstance(value, list):
        for item in value:
            _collect_texts(item, texts, in_body=in_body)


def _matched_lines(entry: dict[str, Any], pattern: re.Pattern[str]) -> list[str]:
    """エントリ内で一致した行を、同一本文の重複を除いて出現順に返す。

    退避された実行結果と可視テキストのように、同一の本文が複数のフィールドへ重複して格納される場合がある。
    また、退避出力だけへ付く行番号接頭辞を別本文の番号なし行と突き合わせる場合がある。
    そのため、別本文に同じ番号なし行がある行番号付き行だけを本文の重複として扱い、表示は最初に現れた原文を保つ。
    """
    texts = _entry_texts(entry)
    unnumbered_lines_by_text = [
        {line_text.strip() for line_text in text.splitlines() if _LINE_NUMBER_PREFIX.match(line_text) is None} for text in texts
    ]
    seen: set[str] = set()
    matched: list[str] = []
    for index, text in enumerate(texts):
        for line_text in text.splitlines():
            stripped = line_text.strip()
            if not pattern.search(line_text):
                continue
            key = stripped
            numbered = _LINE_NUMBER_PREFIX.match(line_text)
            if numbered and any(
                other_index != index and numbered.group(1).strip() in other_lines
                for other_index, other_lines in enumerate(unnumbered_lines_by_text)
            ):
                key = numbered.group(1).strip()
            if key in seen:
                continue
            seen.add(key)
            matched.append(line_text)
    return matched


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
    parser = argparse.ArgumentParser(
        description=(
            "transcriptから振り返り用の時系列証拠を抽出・照会する。--statsは経過時間、トークン消費、"
            "ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を返す。"
            "メイン記録は振り返り境界より前のレコードを対象とし、補助記録は境界より前に起動した"
            "処理単位の全体を帰属させる。stats-toolの合計秒は並列実行分を含むため壁時計時間とは一致しない。"
        )
    )
    parser.add_argument(
        "transcript_path",
        nargs="?",
        help="transcriptの絶対パス。省略時と読み込み失敗時はfallback指示を出力する。",
    )
    parser.add_argument(
        "--warn",
        action="store_true",
        help="エントリ内の全本文（hook通知を含む。管理用フィールドと"
        "本スクリプト自身の実行記録は除く）のうち"
        "警告（`警告`・`warn`、大小文字を区別しない）に一致した行を照会する。",
    )
    parser.add_argument(
        "--grep",
        metavar="REGEX",
        help="エントリ内の全本文（hook通知を含む。管理用フィールドと"
        "本スクリプト自身の実行記録は除く）を正規表現で検索し、"
        "一致行と一致エントリ数を照会する。",
    )
    parser.add_argument(
        "--detail",
        metavar="LINE",
        nargs="+",
        type=int,
        help="指定した行番号のエントリの詳細（tool_useの入力全体・tool_result本文。"
        "本文が退避されている場合はツール実行結果側の本文）を照会する。"
        "出力量の上限で本文を省略したエントリのイベントには`omitted`を付ける。",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="経過時間、トークン消費、ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を照会する。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """証拠または照会結果を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if sum((args.warn, args.grep is not None, args.detail is not None, args.stats)) > 1:
        return _print_error("--warn・--grep・--detail・--statsは併用できない")

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
    if args.stats:
        runtime = _detect_runtime([record.entry for record in records])
        if runtime is None:
            _print_events(_fallback())
            return 0
        _print_events(_stats_events(records, runtime, args.transcript_path or ""))
        return 0

    _print_events(_extract_records(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
