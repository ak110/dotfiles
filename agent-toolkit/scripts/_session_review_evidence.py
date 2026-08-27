#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude CodeとCodexのtranscriptから振り返り用の時系列証拠を抽出し、照会する。

既定モードは時系列イベントをJSONLで出力し、各イベントへ由来行の行番号`line`を付ける。
`--warn`・`--grep`・`--detail`・`--stats`・`--hook-notices`の照会モードは、抽出結果に無い詳細をtranscriptから
1コマンドで取得するためのもので、都度のワンライナーによる再解析を置き換える。

本スクリプトは検査スクリプトではなくデータ抽出ツールであるため、
`agent-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と照会不能（モード併用・不正な正規表現・範囲外の行番号）を終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import collections
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
_WARNING_LINE_PATTERN = re.compile(
    r"^\s*(?:\d+\t)?(?:"
    r"(?:\[auto-generated:[^\]]+\]\s*)?\[(?:warn|warning)\](?:\s|$)|"
    r"⚠(?:\s+|\s*[:：])|"
    r"(?:warning|warn|警告)\s*[:：]"
    r")",
    re.IGNORECASE,
)
_STRUCTURED_WARNING_VALUES = frozenset({"warn", "warning", "警告"})
_STRUCTURED_WARNING_KEYS = frozenset({"warning", "warnings", "warning_message", "warningmessage", "is_warning"})
_STRUCTURED_SEVERITY_KEYS = frozenset({"severity", "level"})
_STRUCTURED_WARNING_BODY_KEYS = ("text", "message", "detail", "description", "output", "content")
_STRUCTURED_WARNING_STREAM_KEYS = ("stdout", "stderr")
_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+\t(.*)$")
_SKILL_INVOCATION_PREFIX = "Base directory for this skill: "
_SELF_SCRIPT_STEM = "_session_review_evidence"
_SEGMENT_SEPARATORS = re.compile(r"[;&|]+")
_PATH_SEPARATORS = re.compile(r"[/\\]")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_NAMES = frozenset({"sh", "bash", "zsh"})
_SCRIPT_RUNNERS = frozenset({"uv", "uvx", "env"})
_PERSISTED_OUTPUT_PREFIX = "<persisted-output>"
_HOOK_RECORD_TYPES = frozenset({"hook_additional_context", "hook_system_message", "hook_blocking_error", "hook_success"})
_HOOK_NOTICE_MARKER = re.compile(r"\[auto-generated:\s*(?P<hook>[^\]]*?)\s*\](?:\s*\[(?P<tag>[^\]]*)\])?")
_HOOK_NOTICE_KIND_LENGTH = 80
# 通知本文の可変部（語頭から始まるパスと、TBD識別子・行番号などの数値）。種別キーの分裂を防ぐため置換する。
# パスは語頭に限定するが、数値列は語頭・語中を問わず置換するため、`github.com/ak110/dotfiles`のような
# 固定の識別子も数値部分が置換される。
_HOOK_NOTICE_VARIABLE = re.compile(r"""(?<![^\s(\[<'"`])~?/[^\s`'"]+|\d+""")
_HOOK_NOTICE_VARIABLE_PLACEHOLDER = "<var>"
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
        # 自由形式の本文を保持するフィールド。内部のキー名はユーザーの入力に由来する
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
    if runtime == "claude" and boundary < len(events) and events[boundary]["kind"] == "session-review-started":
        boundary = len(events)
    events = [event for event in events[:boundary] if event["kind"] != "session-review-started"]

    for event in reversed(events):
        if event["kind"] == "assistant":
            event["kind"] = "final-result"
            break
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def _review_boundary_index(events: list[dict[str, Any]], runtime: _Runtime) -> int:
    """手動・自動振り返り境界から最新の適用可能なイベント位置を返す。"""
    candidates = [
        index
        for index, event in enumerate(events)
        if event["kind"] == "user" and _is_manual_review_invocation(event["text"], runtime)
    ]
    started_indices = [index for index, event in enumerate(events) if event["kind"] == "session-review-started"]
    if runtime == "claude":
        candidates.extend(started_indices)
    else:
        for started_index in started_indices:
            stop_index = next(
                (
                    index
                    for index in range(started_index - 1, -1, -1)
                    if events[index]["kind"] == "user" and events[index]["text"].startswith(STOP_ADVISOR_PREFIX)
                ),
                None,
            )
            if stop_index is not None:
                candidates.append(stop_index)
    return max(candidates, default=len(events))


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
_CLAUDE_HINT_KEYS = ("command", "file_path", "path", "pattern", "url", "query")
_THREAD_ID_KEYS = ("session_id", "sessionId", "threadId", "conversationId")
_AGENTS_SERVER_TOOL_NAMES = frozenset(
    {
        *(f"mcp__plugin_agent-toolkit_agents_server__{name}" for name in ("start", "wait", "send_message", "kill")),
        *(f"mcp__agents_server__{name}" for name in ("start", "wait", "send_message", "kill")),
    }
)
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


def _codex_normalized_tokens(tokens: dict[str, int]) -> dict[str, int]:
    """Codexの内訳をClaude形式の4成分へ意味的に変換する。

    Codexの`input_tokens`はキャッシュ済み入力（`cached_input_tokens`）を内包する総入力であり、
    非キャッシュ入力だけを表すClaude形式の同名キーとは同義ではない。同名のまま合算すると
    キャッシュ済み入力が非キャッシュ入力の欄へ混入する。`total_tokens`は入力と出力の合計であり、
    加算すれば他成分の再合算となる。そのため次の対応で変換した値だけを合算へ用いる。

    - `cache_read_input_tokens` ← `cached_input_tokens`
    - `input_tokens` ← `input_tokens - cached_input_tokens`（内包関係は実測で確認済み）
    - `output_tokens` ← `output_tokens`（`reasoning_output_tokens`は内包されるため加算しない）
    - `cache_creation_input_tokens` ← `cache_write_input_tokens`
    """
    cached = tokens.get("cached_input_tokens", 0)
    return {
        "input_tokens": max(tokens.get("input_tokens", 0) - cached, 0),
        "output_tokens": tokens.get("output_tokens", 0),
        "cache_creation_input_tokens": tokens.get("cache_write_input_tokens", 0),
        "cache_read_input_tokens": cached,
    }


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


def _codex_token_usages(records: list[_Record]) -> list[tuple[_Record, dict[str, int]]]:
    """各`token_count`レコードの`info.last_token_usage`（1リクエストの実消費）を返す。

    同じレコードの`info.total_token_usage`はセッション内の累積値だが、Codexは過去のチェックポイントへ
    巻き戻すと累積器を巻き戻し先の値へ戻して再累積する。巻き戻し後の値には巻き戻し先までの
    消費が既に含まれるため、減少を境界とみなして減少前の値を加算すると当該プレフィックスを二重計上する
    （実測: 累積が`1246611`から`579472`へ減少した記録で、減少後の値から同レコードの
    `last_token_usage.total_tokens`を引いた`490803`が8レコード前の累積値と一致した。
    区間合算方式では実消費`3086405`に対し`3577208`を報告していた）。
    `last_token_usage`は1リクエスト当たりの実消費であり、巻き戻しの有無にかかわらず単純加算で
    セッション全体の消費量が得られる（実測: 走査した4398 rolloutの全`token_count`レコードに存在する）。

    ただしCodexは同一リクエストの`token_count`を複数回記録する（ターン終了時の再送、compact直後の
    記録など。後者は`last_token_usage`の6成分が全て0となる）。重複記録では`total_token_usage`が
    直前の採用レコードと完全に一致するため、一致するレコードを加算対象から除外する
    （実測: `~/.codex/sessions/2026/08/`配下1942セッションのうち707セッションで無条件加算が実消費を
    上回り、最大54.8%の過大計上となった。除外方式を実rollout 476件へ適用すると474件で加算値が
    セッション内の最終`total_token_usage`と一致した）。巻き戻しでは`total_token_usage`が直前と
    異なる値へ変わるため、減少後のレコードは加算対象へ残る。
    `total_token_usage`がdictでないか`total_tokens`を欠くレコードは判別条件を適用できないため、
    安全側として常に加算対象へ含める。
    """
    usages: list[tuple[_Record, dict[str, int]]] = []
    previous_total: dict[str, Any] | None = None
    for record in records:
        payload = record.entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        last_usage = info.get("last_token_usage")
        if not isinstance(last_usage, dict):
            continue
        total_usage = info.get("total_token_usage")
        comparable = isinstance(total_usage, dict) and "total_tokens" in total_usage
        if comparable and previous_total is not None and total_usage == previous_total:
            continue
        previous_total = total_usage if comparable else None
        usages.append((record, {key: _token_value(last_usage.get(key)) for key in _CODEX_TOKEN_KEYS}))
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
    tokens = {key: 0 for key in _CODEX_TOKEN_KEYS}
    for _, usage in usages:
        _add_tokens(tokens, usage)
    summary.update(
        tokens=tokens,
        max_context_tokens=max(usage["input_tokens"] for _, usage in usages),
        api_messages=len(usages),
    )
    return summary


def _stats_boundary_line(records: list[_Record], runtime: _Runtime) -> int | None:
    events = _extract_for_runtime([record.entry for record in records], runtime, [record.line for record in records])
    boundary = _review_boundary_index(events, runtime)
    if runtime == "claude" and boundary < len(events) and events[boundary]["kind"] == "session-review-started":
        return None
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
) -> tuple[list[tuple[str, str | None, list[_Record]]], int, int]:
    """サブエージェント記録の選択結果、除外件数、列挙した記録ファイル数を返す。

    記録ファイル数は境界判定・振り返り系除外を適用する前の母集団の大きさであり、
    主体別集計イベントを出力するかどうかの判定に用いる。
    選択結果が0件でも記録自体が存在する場合は、除外の実数を含む合算イベントを出力する必要がある。
    """
    path = Path(transcript_path)
    subagent_dir = path.with_suffix("") / "subagents"
    try:
        paths = sorted(subagent_dir.glob("agent-*.jsonl"))
    except OSError:
        return [], 0, 0
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
        parent_id = raw_meta.get("parentAgentId") if isinstance(raw_meta.get("parentAgentId"), str) else None
        # 記録ファイル名は`agent-<parentAgentId>`の形式のため、親の照合キーをファイル名由来の形式へ揃える。
        parent = f"agent-{parent_id}" if parent_id else None
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
    return selected, excluded_count, len(paths)


def _thread_id_from_mapping(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in _THREAD_ID_KEYS:
        thread = value.get(key)
        if isinstance(thread, str) and thread:
            return thread
    return None


def _thread_ids_from_record(record: _Record) -> list[tuple[_Runtime, str]]:
    """Claude transcriptとCodex rolloutからエンジン付きsession識別子を抽出する。"""
    entry = record.entry
    found: list[tuple[_Runtime, str]] = [("codex", thread_id) for thread_id in _native_agent_thread_ids(entry)]

    def add_mapping(value: Any, default_engine: _Runtime) -> None:
        mapping = value if isinstance(value, dict) else _json_object(value)
        if not isinstance(mapping, dict):
            return
        session_id = _thread_id_from_mapping(mapping)
        if not session_id:
            return
        engine = mapping.get("engine")
        if engine == "claude":
            chosen_engine: _Runtime = "claude"
        elif engine == "codex":
            chosen_engine = "codex"
        else:
            chosen_engine = default_engine
        found.append((chosen_engine, session_id))

    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") in _AGENTS_SERVER_TOOL_NAMES:
                add_mapping(block.get("input"), "claude")

    mcp_meta = entry.get("mcpMeta")
    if isinstance(mcp_meta, dict):
        add_mapping(mcp_meta.get("structuredContent"), "claude")

    tool_result = entry.get("toolUseResult")
    add_mapping(tool_result, "claude")

    payload = entry.get("payload")
    if isinstance(payload, dict) and payload.get("type") in {"custom_tool_call", "custom_tool_call_output"}:
        name = payload.get("name")
        if payload.get("type") == "custom_tool_call" and name in _AGENTS_SERVER_TOOL_NAMES:
            add_mapping(payload.get("arguments") or payload.get("input"), "codex")
        if payload.get("type") == "custom_tool_call_output":
            add_mapping(payload.get("output"), "codex")

    notification_texts: list[str] = []
    if entry.get("type") == "queue-operation":
        notification_texts.extend(_text_blocks(entry.get("content")))
    notification_texts.extend(_text_blocks(content))
    for text in notification_texts:
        for match in _TASK_RESULT_PATTERN.finditer(text):
            add_mapping(match.group(1), "claude")
    return list(dict.fromkeys(found))


def _native_agent_thread_ids(value: Any) -> list[str]:
    """Codexの`SubAgentActivity.agent_thread_id`を構造化フィールドから再帰取得する。"""
    found: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "SubAgentActivity":
            thread_id = value.get("agent_thread_id")
            if isinstance(thread_id, str) and thread_id:
                found.append(thread_id)
        for item in value.values():
            found.extend(_native_agent_thread_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_native_agent_thread_ids(item))
    return list(dict.fromkeys(found))


def _rollout_path(thread_id: str) -> Path | None:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    escaped_thread = re.escape(thread_id)
    candidates = sorted(
        path
        for path in codex_home.glob(f"sessions/*/*/*/rollout-*{thread_id}.jsonl")
        if re.search(rf"rollout-.*{escaped_thread}\.jsonl$", path.name)
    )
    return candidates[0] if candidates else None


def _claude_transcript_path(session_id: str) -> Path | None:
    """Claude Code transcriptのsession_idに対応するJSONLを探す。"""
    projects = Path.home() / ".claude" / "projects"
    candidates = sorted(projects.glob(f"**/{session_id}.jsonl"))
    return candidates[0] if candidates else None


def _session_path(engine: _Runtime, session_id: str) -> Path | None:
    return _rollout_path(session_id) if engine == "codex" else _claude_transcript_path(session_id)


def _stats_thread_records(
    main_records: list[_Record],
    subagents: list[tuple[str, str | None, list[_Record]]],
    boundary_timestamp: datetime.datetime | None = None,
) -> dict[tuple[_Runtime, str], tuple[int, str | None]]:
    """主記録・補助記録から委譲先sessionをエンジン別に再帰列挙する。

    直接の委譲先だけでなく、rollout内の`SubAgentActivity`から得られる子孫も探索する。
    同じrolloutを複数の親が参照しても一度だけ処理し、循環した委譲木で停止しない。
    境界超過で除外したthreadは`excluded`へ記録し、別の親から再発見されても`threads`へ戻さない
    （`visited`済みのthreadは再処理時に無条件で`continue`するため、`threads`への再挿入だけを
    別途防がないと最終結果へ復帰する）。
    """
    threads: dict[tuple[_Runtime, str], tuple[int, str | None]] = {}
    excluded: set[tuple[_Runtime, str]] = set()
    pending: list[tuple[_Runtime, str, int, str | None]] = []

    def add(engine: _Runtime, session_id: str, line: int, agent_id: str | None) -> None:
        key = (engine, session_id)
        if key not in threads and key not in excluded:
            threads[key] = (line, agent_id)
            pending.append((engine, session_id, line, agent_id))

    for record in main_records:
        for engine, session_id in _thread_ids_from_record(record):
            add(engine, session_id, record.line, None)
    for agent_id, _, records in subagents:
        for record in records:
            for engine, session_id in _thread_ids_from_record(record):
                add(engine, session_id, record.line, agent_id)

    visited: set[tuple[_Runtime, str]] = set()
    while pending:
        engine, session_id, line, agent_id = pending.pop(0)
        key = (engine, session_id)
        if key in visited:
            continue
        visited.add(key)
        transcript = _session_path(engine, session_id)
        if transcript is None:
            continue
        session_records = _load_records(str(transcript))
        if session_records is None:
            continue
        if boundary_timestamp is not None:
            starts = [_record_timestamp(record) for record in session_records]
            start = min((value for value in starts if value is not None), default=None)
            if start is None or start >= boundary_timestamp:
                threads.pop(key, None)
                excluded.add(key)
                continue
        for record in session_records:
            for child_id in _native_agent_thread_ids(record.entry):
                add("codex", child_id, line, agent_id)
    return threads


def _claude_call_hint(block_input: Any) -> str | None:
    """tool_use入力から反復照会の識別に用いる代表的な対象値を取得する。

    `command`を持たないツール（`Read`・`Edit`・`Grep`など）では、対象を表す入力キーを
    定義順に探す。列挙は全ツール種別の網羅を目的とせず、取得できた値だけをヒントとする。
    いずれのキーも持たない呼び出しはヒントなしとし、反復集計の対象から外れる。

    値は先頭行への切り詰めも文字数の切り詰めも行わず全体を返す。複数行のコマンドは先頭行が
    `cd <ディレクトリ>`・変数代入・ヒアドキュメント開始行などで一致しやすく、
    先頭行だけをヒントにすると内容の異なる呼び出しが同じ反復組へ集まるためである。
    同じ理由で文字数の切り詰めも反復判定より後段（表示時）へ置く。
    """
    if not isinstance(block_input, dict):
        return None
    for key in _CLAUDE_HINT_KEYS:
        value = block_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _codex_call_hint(payload: dict[str, Any]) -> str | None:
    """Codexのツール呼び出しpayloadから反復照会の識別に用いる対象値を取得する。

    実行内容の格納先は呼び出しの種類で異なり、`arguments`のJSONへ`command`又は`cmd`を持つ
    呼び出しと、`arguments`を持たず自由形式の`input`へ実行内容を埋め込む呼び出し（`exec`など）が
    実在する。前者から取得できない場合は`input`の文字列をそのままヒントとする。
    値は`_claude_call_hint`と同じ理由で先頭行へも文字数へも切り詰めず全体を返す。
    """
    arguments = _json_object(payload.get("arguments"))
    if arguments is not None:
        for key in ("command", "cmd"):
            command = arguments.get(key)
            if isinstance(command, list):
                joined = " ".join(part for part in command if isinstance(part, str))
                if joined.strip():
                    return joined.strip()
            elif isinstance(command, str) and command.strip():
                return command.strip()
    value = payload.get("input")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _stats_call_entries(records: list[_Record], runtime: _Runtime) -> list[dict[str, Any]]:
    """ツール呼び出しと結果を対応付け、所要時間と入力ヒントを持つ呼び出しエントリを返す。

    エントリは表示用の`hint`（`_clip`で切り詰めた値）と、反復判定用の`hint_key`（切り詰め前の原文）を
    分けて持つ。切り詰め後の値で反復を判定すると、上限まで前方一致するだけの別内容の呼び出しが
    同じ反復組へ集約されるためである（実測: 上限2000文字の一致で内容の異なる組が実記録に存在する）。
    """
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
                    hint = _claude_call_hint(block.get("input"))
                    calls.setdefault(block["id"], (str(block.get("name", "")), hint, record.line, timestamp))
                elif block_type == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    results.setdefault(block["tool_use_id"], []).append(timestamp)
            continue

        payload = record.entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if payload_type in {"custom_tool_call", "function_call"} and isinstance(payload.get("call_id"), str):
            hint = _codex_call_hint(payload)
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
            item["hint"] = _clip(hint)
            item["hint_key"] = hint
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
        for record, raw in _codex_token_usages(records):
            # `cached_input_tokens`が`input_tokens`へ内包される関係は`last_token_usage`でも同じであり、
            # 変換しないとキャッシュ済み入力が非キャッシュ入力の欄へ混入し、
            # 同じ走行の`stats-total`（変換済み）と数値が矛盾する。
            normalized = _codex_normalized_tokens(raw)
            candidates.append(
                {
                    "total_tokens": _token_total(normalized),
                    **normalized,
                    "line": record.line,
                    "new_tokens": normalized["output_tokens"] + normalized["cache_creation_input_tokens"],
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
    """振り返り境界を適用した集計イベント列を返す。

    `stats-total`はメイン記録・全サブエージェント記録・全Codexスレッドの3区分の合算とする。
    3区分は記録ファイルが互いに排他であり、トークンが重複しない。
    Codex形式の内訳はClaude形式と成分の意味が異なるため、合算前に`_codex_normalized_tokens`で
    4成分へ変換する。メイン記録自体がCodex形式である場合も同じ変換を適用する。
    変換は成分ごとの加減算だけで構成され、`cached_input_tokens`は各レコードで`input_tokens`へ
    内包されるため、レコード単位で変換してから合算した値と、合算してから変換した値は一致する。
    個別表示の`stats-summary`と`stats-codex-thread`はCodexの全成分をそのまま表示する。
    """
    main_records, boundary_timestamp = _stats_main_records(records, runtime)
    summary = _stats_summary_data(main_records, runtime)
    subagents: list[tuple[str, str | None, list[_Record]]] = []
    excluded_review_agents = 0
    subagent_record_count = 0
    if runtime == "claude":
        subagents, excluded_review_agents, subagent_record_count = _stats_subagent_records(transcript_path, boundary_timestamp)
    threads = _stats_thread_records(main_records, subagents, boundary_timestamp)
    thread_summaries: list[tuple[_Runtime, str, dict[str, Any], int, str | None]] = []
    for (engine, session_id), (line, agent_id) in threads.items():
        transcript = _session_path(engine, session_id)
        if transcript is None:
            continue
        session_records = _load_records(str(transcript))
        if session_records is None:
            continue
        thread_summary = _stats_summary_data(session_records, engine)
        thread_summaries.append((engine, session_id, thread_summary, line, agent_id))

    total_tokens: dict[str, int] = {}
    if isinstance(summary.get("tokens"), dict):
        _add_tokens(total_tokens, _codex_normalized_tokens(summary["tokens"]) if runtime == "codex" else summary["tokens"])
    for _, _, subagent_records in subagents:
        sub_summary = _stats_summary_data(subagent_records, "claude")
        if isinstance(sub_summary.get("tokens"), dict):
            _add_tokens(total_tokens, sub_summary["tokens"])
    for engine, _, thread_summary, _, _ in thread_summaries:
        if isinstance(thread_summary.get("tokens"), dict):
            _add_tokens(
                total_tokens,
                _codex_normalized_tokens(thread_summary["tokens"]) if engine == "codex" else thread_summary["tokens"],
            )

    thread_counts: dict[str, int] = collections.Counter(engine for engine, _, _, _, _ in thread_summaries)

    total_event: dict[str, Any] = {
        "kind": "stats-total",
        "tokens": total_tokens,
        "subagent_count": len(subagents),
        "agent_thread_count": len(thread_summaries),
        "agent_thread_counts": dict(sorted(thread_counts.items())),
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
    # 入力ヒントを取れない呼び出しは対象が異なっても同じ組へ集まり、
    # 反復照会の実態と異なる件数を報告する。集計対象から除く。
    repeats: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for call in calls:
        hint_key = call.get("hint_key")
        if hint_key:
            repeats.setdefault((call["tool"], hint_key), []).append(call)
    events.extend(
        {
            "kind": "stats-repeat",
            "tool": tool,
            "hint": items[0]["hint"],
            "count": len(items),
            "lines": [item["line"] for item in items],
        }
        for (tool, _), items in sorted(
            ((key, items) for key, items in repeats.items() if len(items) >= 2),
            key=lambda item: (-len(item[1]), item[0]),
        )[:10]
    )
    for call in sorted(calls, key=lambda item: (-item["seconds"], item["line"]))[:10]:
        event = {"kind": "stats-slow-call", "tool": call["tool"], "seconds": round(call["seconds"], 1), "line": call["line"]}
        if call.get("hint"):
            event["hint"] = call["hint"]
        events.append(event)
    events.extend({"kind": "stats-token-peak", **peak} for peak in _stats_token_peaks(main_records, runtime))

    # 記録ファイルが1件でもあれば、全件が振り返り系・境界外で選択0件になっても
    # 除外の実数を`excluded_review_agents`で観測できるよう合算イベントを出力する。
    if subagent_record_count:
        subagent_rows: list[tuple[str, str | None, dict[str, Any]]] = []
        subagent_total: dict[str, int] = {key: 0 for key in _CLAUDE_TOKEN_KEYS}
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

    for engine, session_id, thread_summary, line, agent_id in sorted(
        thread_summaries,
        key=lambda item: (-item[2].get("tokens", {}).get("total_tokens", 0), item[1]),
    ):
        # `line`はメインtranscriptの行番号を指す`--detail`用の値であるため、
        # サブエージェント記録から見つけたスレッド（`agent_id`が非null）では付けない。
        thread_event: dict[str, Any] = {
            "kind": "stats-agent-thread",
            "engine": engine,
            "session_id": session_id,
            "thread": session_id,
            **thread_summary,
        }
        if agent_id:
            thread_event["agent"] = agent_id
        else:
            thread_event["line"] = line
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


def _structured_warning_fields(value: dict[str, Any]) -> tuple[list[Any], bool]:
    """辞書から警告キーの値と直接警告を表す標識を取り出す。"""
    warning_values: list[Any] = []
    direct_warning = False
    for key, item in value.items():
        normalized_key = key.casefold() if isinstance(key, str) else ""
        if normalized_key in _STRUCTURED_WARNING_KEYS:
            if normalized_key == "is_warning":
                direct_warning |= item is True
            elif item is True:
                direct_warning = True
            elif item not in (None, "", [], {}):
                warning_values.append(item)
            continue
        if (
            normalized_key in _STRUCTURED_SEVERITY_KEYS
            and isinstance(item, str)
            and item.casefold() in _STRUCTURED_WARNING_VALUES
        ):
            direct_warning = True
        if normalized_key in {"type", "kind"} and isinstance(item, str) and item.casefold() in _STRUCTURED_WARNING_VALUES:
            direct_warning = True
    return warning_values, direct_warning


def _structured_warning_value_texts(value: Any) -> list[str]:
    """構造化警告の値又は直接警告辞書から本文だけを取り出す。"""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return [value] if value.strip() else []
        if isinstance(parsed, (dict, list)):
            return _structured_warning_value_texts(parsed)
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _structured_warning_value_texts(item)]
    if not isinstance(value, dict):
        return []

    warning_values, _ = _structured_warning_fields(value)
    if warning_values:
        return [text for item in warning_values for text in _structured_warning_value_texts(item)]
    normalized = {key.casefold(): item for key, item in value.items() if isinstance(key, str)}
    for key in _STRUCTURED_WARNING_BODY_KEYS:
        if key in normalized:
            return _structured_warning_value_texts(normalized[key])
    stream_values = [normalized[key] for key in _STRUCTURED_WARNING_STREAM_KEYS if key in normalized]
    if stream_values:
        return [text for item in stream_values for text in _structured_warning_value_texts(item)]
    return [text for item in value.values() if isinstance(item, (dict, list)) for text in _structured_warning_value_texts(item)]


def _warning_hook_records(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """入力本文を除外してhook通知の記録だけを集める。"""
    found: list[dict[str, Any]] = []

    def collect(value: Any, *, in_body: bool = False) -> None:
        if isinstance(value, dict):
            if not in_body and value.get("type") in _HOOK_RECORD_TYPES:
                found.append(value)
                return
            for key, item in value.items():
                collect(item, in_body=in_body or key in _BODY_KEYS)
        elif isinstance(value, list):
            for item in value:
                collect(item, in_body=in_body)

    collect(entry)
    return found


def _warning_result_values(entry: dict[str, Any]) -> list[Any]:
    """構造化警告を抽出できる実行結果領域の値だけを返す。"""
    values: list[Any] = []

    if "toolUseResult" in entry:
        values.append(entry["toolUseResult"])

    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        values.extend(block for block in content if isinstance(block, dict) and block.get("type") == "tool_result")

    payload = entry.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "function_call_output":
        values.append(payload.get("output"))
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
        values.append(payload.get("output"))
    if isinstance(payload, dict) and payload.get("type") == "event_msg":
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "CommandExecution":
            values.extend(item.get(key) for key in ("aggregated_output", "output", "stdout", "stderr"))

    values.extend(_warning_hook_records(entry))
    return values


def _warning_texts(entry: dict[str, Any]) -> list[str]:
    """実行結果領域内の行頭マーカー又は構造化警告フィールドに対応する本文行を返す。"""
    bodies: list[tuple[str, bool]] = []

    def collect_markers(value: Any) -> None:
        if isinstance(value, str):
            bodies.append((value, True))
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(parsed, (dict, list)):
                collect_markers(parsed)
            return
        if isinstance(value, dict):
            for item in value.values():
                collect_markers(item)
        elif isinstance(value, list):
            for item in value:
                collect_markers(item)

    def collect_structured(value: Any) -> None:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(parsed, (dict, list)):
                collect_structured(parsed)
            return
        if isinstance(value, dict):
            warning_values, direct_warning = _structured_warning_fields(value)
            for warning_value in warning_values:
                for text in _structured_warning_value_texts(warning_value):
                    bodies.append((text, False))
            if direct_warning and not warning_values:
                for text in _structured_warning_value_texts(value):
                    bodies.append((text, False))
            for item in value.values():
                collect_structured(item)
        elif isinstance(value, list):
            for item in value:
                collect_structured(item)

    for result_value in _warning_result_values(entry):
        collect_markers(result_value)
        collect_structured(result_value)
    unnumbered_by_body = [
        {line.strip() for line in text.splitlines() if _LINE_NUMBER_PREFIX.match(line) is None} for text, _ in bodies
    ]
    seen: set[str] = set()
    result: list[str] = []
    for body_index, (text, marker_only) in enumerate(bodies):
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or not (not marker_only or _WARNING_LINE_PATTERN.search(line)):
                continue
            numbered = _LINE_NUMBER_PREFIX.match(line)
            key = stripped
            if numbered:
                normalized = numbered.group(1).strip()
                if any(
                    other_index != body_index and normalized in other_lines
                    for other_index, other_lines in enumerate(unnumbered_by_body)
                ):
                    key = normalized
            if key in seen:
                continue
            seen.add(key)
            result.append(line)
    return result


def _warning_boundary_line(records: list[_Record]) -> int | None:
    """警告抽出へ適用する振り返り境界のtranscript行番号を返す。"""
    runtime = _detect_runtime([record.entry for record in records])
    if runtime is None:
        return None
    events = _extract_for_runtime([record.entry for record in records], runtime, [record.line for record in records])
    boundary = _review_boundary_index(events, runtime)
    if boundary >= len(events):
        return None
    for event in events[boundary:]:
        line = event.get("line")
        if isinstance(line, int):
            return line
    return None


def _warning_hook_identities(entry: dict[str, Any]) -> dict[str, list[str]]:
    """hook記録の警告本文ごとにツール呼び出し識別子を出現順で返す。"""
    identities: dict[str, list[str]] = {}
    for hook_record in _warning_hook_records(entry):
        tool_use_id = hook_record.get("toolUseID")
        if not isinstance(tool_use_id, str):
            continue
        for line_text in _warning_texts(hook_record):
            tool_use_ids = identities.setdefault(line_text, [])
            if tool_use_id not in tool_use_ids:
                tool_use_ids.append(tool_use_id)
    return identities


def _warning_events(records: list[_Record]) -> list[dict[str, Any]]:
    """振り返り境界より前の実行時警告を行番号付きで返す。

    同じhook通知は成功記録と追加コンテキストへ重複して格納されるため、
    ツール呼び出し識別子と本文の組で1件として扱う。
    識別子を持たない警告はコマンド出力由来の検出を失わないように個別に保持する。
    一致しない場合はその事実を返す。
    """
    events: list[dict[str, Any]] = []
    seen_hook_warnings: set[tuple[str, str]] = set()
    boundary_line = _warning_boundary_line(records)
    scannable = _scannable_records(records)
    if boundary_line is not None:
        scannable = [record for record in scannable if record.line < boundary_line]
    for record in scannable:
        matched_lines = _warning_texts(record.entry)
        if not matched_lines:
            continue
        hook_identities = _warning_hook_identities(record.entry)
        hint = _tool_hint(record.entry)
        for line_text in matched_lines:
            tool_use_ids = hook_identities.get(line_text, [])
            if not tool_use_ids:
                event: dict[str, Any] = {"kind": "warning", "line": record.line, "text": _clip(line_text)}
                if hint:
                    event["tool"] = hint
                events.append(event)
                continue
            for tool_use_id in tool_use_ids:
                identity = (tool_use_id, line_text)
                if identity in seen_hook_warnings:
                    continue
                seen_hook_warnings.add(identity)
                event = {"kind": "warning", "line": record.line, "text": _clip(line_text)}
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


class _HookNoticeKey(NamedTuple):
    """通知の分類軸。標識を持たない通知では`hook`と`tag`が`None`になる。"""

    hook: str | None
    hook_name: str | None
    tag: str | None
    kind_text: str


def _hook_notice_events(records: list[_Record]) -> list[dict[str, Any]]:
    """hook実行の記録から通知本文だけを集計し、分類軸ごとの件数を件数降順で返す。

    母集団はhook実行の記録4種であり、`--warn`のような本文への文字列一致は用いない。
    hookの発動を伴わない本文（ソースの引用、会話中の言及）は記録の種別で除かれる。
    同一ツール呼び出しの通知は実行成功記録の標準出力と追加コンテキストの双方へ格納されるため、
    分類軸とツール呼び出し識別子の組で重複を除いてから数える。
    """
    seen: set[tuple[str | None, _HookNoticeKey]] = set()
    counts: collections.Counter[_HookNoticeKey] = collections.Counter()
    for record in records:
        for hook_record in _hook_records(record.entry):
            tool_use_id = hook_record.get("toolUseID")
            hook_name = hook_record.get("hookName")
            for body in _hook_notice_bodies(hook_record):
                key = _hook_notice_key(body, hook_name if isinstance(hook_name, str) else None)
                if key is None:
                    continue
                identity = (tool_use_id if isinstance(tool_use_id, str) else None, key)
                if identity in seen:
                    continue
                seen.add(identity)
                counts[key] += 1
    events: list[dict[str, Any]] = [
        {
            "kind": "hook-notice",
            "hook": key.hook,
            "hook_name": key.hook_name,
            "tag": key.tag,
            "kind_text": key.kind_text,
            "count": count,
        }
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], tuple(str(part) for part in item[0])))
    ]
    events.append({"kind": "summary", "count": sum(counts.values())})
    return events


def _hook_records(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """エントリを再帰的にたどり、hook実行の記録を出現順に集める。

    記録の格納先は`attachment`配下などruntimeの版で変わるため、位置ではなく`type`で判定する。
    """
    found: list[dict[str, Any]] = []
    _collect_hook_records(entry, found)
    return found


def _collect_hook_records(value: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if value.get("type") in _HOOK_RECORD_TYPES:
            found.append(value)
        for item in value.values():
            _collect_hook_records(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_hook_records(item, found)


def _hook_notice_bodies(hook_record: dict[str, Any]) -> list[str]:
    """hook実行の記録から通知本文を、記録の種別に応じた格納先から取り出す。

    実行成功記録は標準出力の追加コンテキストと標準エラー出力の双方を通知の格納先とする。
    追加コンテキストを伴わずに標準エラー出力だけで警告を返すhookがあるため、両方を対象とする。
    """
    record_type = hook_record.get("type")
    content = hook_record.get("content")
    if record_type == "hook_additional_context":
        return [item for item in content if isinstance(item, str)] if isinstance(content, list) else []
    if record_type == "hook_system_message":
        return [content] if isinstance(content, str) else []
    if record_type == "hook_blocking_error":
        blocking_error = hook_record.get("blockingError")
        body = blocking_error.get("blockingError") if isinstance(blocking_error, dict) else None
        return [body] if isinstance(body, str) else []
    bodies: list[str] = []
    stdout = _json_object(hook_record.get("stdout"))
    specific_output = stdout.get("hookSpecificOutput") if stdout is not None else None
    additional_context = specific_output.get("additionalContext") if isinstance(specific_output, dict) else None
    if isinstance(additional_context, str):
        bodies.append(additional_context)
    stderr = hook_record.get("stderr")
    if isinstance(stderr, str):
        bodies.append(stderr)
    return bodies


def _hook_notice_key(body: str, hook_name: str | None) -> _HookNoticeKey | None:
    """通知本文を、hook識別子・タグ・正規化した種別へ分解する。空の本文は`None`を返す。

    標識を持たない本文は識別子とタグを`None`とし、発動元と種別だけで分類する。
    種別は、標識を除いた本文の連続する空白を単一の空白へ正規化し、
    対象パスや識別子などの可変部を固定の記号へ置換した先頭一定長とする。
    可変部を残すと同種の通知が複数の種別へ分かれ、
    長さが不足すると別判定の通知が同一種別へ統合されるため、長さは実測に基づいて確定する。
    """
    normalized = " ".join(body.split())
    if not normalized:
        return None
    matched = _HOOK_NOTICE_MARKER.match(normalized)
    hook = matched.group("hook") if matched is not None else None
    tag = matched.group("tag") if matched is not None else None
    text = normalized[matched.end() :].strip() if matched is not None else normalized
    kind_text = _HOOK_NOTICE_VARIABLE.sub(_HOOK_NOTICE_VARIABLE_PLACEHOLDER, text)
    return _HookNoticeKey(hook or None, hook_name, tag or None, kind_text[:_HOOK_NOTICE_KIND_LENGTH])


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
    `mode`・`status`のような汎用語のキーがユーザーの入力そのものを保持するため、
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
            "サブエージェント別集計とCodexスレッド別集計はClaude Code形式のtranscriptでのみ出力する。"
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
        help="振り返り境界より前のエントリから、行頭の警告マーカーまたは"
        "構造化された警告フィールドを持つ実行時警告だけを照会する。任意文字列の検索は`--grep`を使う。",
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
        help="経過時間、トークン消費、ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を照会する。"
        "サブエージェント別集計とCodexスレッド別集計はClaude Code形式のtranscriptでのみ出力する。",
    )
    parser.add_argument(
        "--hook-notices",
        action="store_true",
        help="hook実行の記録（追加コンテキスト・システムメッセージ・遮断エラー・実行成功）に"
        "格納された通知本文だけを集計し、hook識別子・発動元・タグ・種別ごとの件数と"
        "重複を除いた通知件数を照会する。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """証拠または照会結果を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if sum((args.warn, args.grep is not None, args.detail is not None, args.stats, args.hook_notices)) > 1:
        return _print_error("--warn・--grep・--detail・--stats・--hook-noticesは併用できない")

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
    if args.hook_notices:
        _print_events(_hook_notice_events(records))
        return 0

    _print_events(_extract_records(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
