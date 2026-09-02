#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude CodeとCodexのtranscriptから振り返り用の時系列証拠を抽出し、照会する。

既定モードはセッション全体の時系列イベントをJSONLで出力し、各イベントへ由来行の行番号`line`を付ける。
`--warn`・`--grep`・`--detail`・`--stats`・`--hook-notices`の照会モードは、抽出結果に無い詳細をtranscriptから
1コマンドで取得するためのもので、都度のワンライナーによる再解析を置き換える。
`--bundle`の集約実行は、通常表示と`--warn`・`--stats`・`--hook-notices`の走査を1回の記録読み込みでまとめて行い、
走査ごとの全量を指定ディレクトリ配下のファイルへ書いて標準出力へは要約だけを返す。

本スクリプトは検査スクリプトではなくデータ抽出ツールであるため、
`agent-toolkit:agent-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と照会不能（対象記録の読込不能・モード併用・不正な正規表現・範囲外の行番号）を
終了コード2とする区分だけを踏襲する。
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
    r"^(?:"
    r"\s*(?:\d+\t)?(?:"
    r"(?:\[auto-generated:[^\]]+\]\s*)?\[(?:warn|warning)\](?:\s|$)|"
    r"⚠(?:\s+|\s*[:：])"
    r")|"
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
_SELF_SCRIPT_STEM = "session_review_evidence"
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
_FALLBACK_TEXT = (
    "記録は読み込めたが形式を判定できないため抽出証拠を生成できない。"
    "継承した会話履歴を評価し、取得できない範囲を未検証と明記すること。"
)
_CLAUDE_ONLY_NOTE = "集計の母集団はClaude Code形式の記録に限られ、Codex形式の記録からは件数が上がらない。"
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


class _Record(NamedTuple):
    """transcriptの1エントリと、その由来行の1始まり行番号・原文。"""

    line: int
    text: str
    entry: dict[str, Any]


class _CollectedRecord(NamedTuple):
    """全照会モードが共有する、由来と識別子を持つ1記録。"""

    record_id: str
    path: Path
    records: list[_Record]
    runtime: _Runtime | None
    source_record: str | None
    source_line: int | None
    agent_type: str | None
    role: Literal["main", "subagent", "session"]


class _UnresolvedRecord(NamedTuple):
    """委譲識別子は得られたが正本を読み込めなかった記録。"""

    record_id: str
    line: int


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


def _is_subagent_record(entries: list[dict[str, Any]]) -> bool:
    """記録全体が1件のサブエージェントの会話かを返す。"""
    conversation = [entry for entry in entries if entry.get("type") in {"user", "assistant"}]
    return bool(conversation) and all(entry.get("isSidechain") is True for entry in conversation)


def _extract_claude(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """Claude Code形式を由来別の共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    pending_question_lines: dict[str, int] = {}
    subagent_record = _is_subagent_record(entries)
    for line, entry in zip(lines, entries, strict=True):
        for event in _claude_entry_events(entry, line, pending_question_lines, subagent_record):
            event.setdefault("line", line)
            events.append(event)
    return events


def _claude_entry_events(
    entry: dict[str, Any],
    line: int,
    pending_question_lines: dict[str, int],
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


def _finalize(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """最終結果への置換と連番付けを行う。"""
    for event in reversed(events):
        if event["kind"] == "assistant":
            event["kind"] = "final-result"
            break
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def extract(entries: list[dict[str, Any]], lines: list[int] | None = None) -> list[dict[str, Any]]:
    """transcript形式を判定し、セッション全体の対象イベントを順序どおり抽出する。

    `lines`はエントリごとのtranscript行番号。省略時はエントリの並び順を行番号とみなす。
    """
    if not entries:
        return []
    runtime = _detect_runtime(entries)
    if runtime is None:
        return _fallback()
    return _finalize(_extract_for_runtime(entries, runtime, lines))


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


def load_and_extract(raw_path: str) -> list[dict[str, Any]]:
    """絶対パスのJSONLを一度読み、抽出結果を返す。"""
    records = _load_records(raw_path)
    if records is None:
        raise ValueError(f"対象記録を読み込めない: {raw_path}")
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


def _parse_timestamp(value: str) -> datetime.datetime:
    """ISO 8601の時刻を解析し、タイムゾーン無しの値をUTCとして返す。"""
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _record_timestamp(record: _Record) -> datetime.datetime | None:
    value = record.entry.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        return _parse_timestamp(value)
    except ValueError:
        return None


def _apply_observation_boundary(records: list[_Record], boundary: datetime.datetime) -> list[_Record]:
    """時刻無しと境界以前の親記録を、元の行番号を保って返す。"""
    return [record for record in records if (timestamp := _record_timestamp(record)) is None or timestamp <= boundary]


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


def _thread_id_from_mapping(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in _THREAD_ID_KEYS:
        thread = value.get(key)
        if isinstance(thread, str) and thread:
            return thread
    return None


def _agents_server_call_ids(records: list[_Record]) -> set[str]:
    """Codexの呼び出し入力にagents_server名を含む呼び出しIDを返す。"""
    call_ids: set[str] = set()
    for record in records:
        payload = record.entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") not in {"custom_tool_call", "function_call"}:
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            continue
        values = (payload.get("input"), payload.get("arguments"))
        if any(
            isinstance(value, str) and any(tool_name in value for tool_name in _AGENTS_SERVER_TOOL_NAMES) for value in values
        ):
            call_ids.add(call_id)
    return call_ids


def _thread_ids_from_record(
    record: _Record,
    agents_server_call_ids: set[str],
) -> list[tuple[_Runtime | None, str]]:
    """Claude transcriptとCodex rolloutからsession識別子と実行系ヒントを抽出する。"""
    entry = record.entry
    found: list[tuple[_Runtime | None, str]] = [("codex", thread_id) for thread_id in _native_agent_thread_ids(entry)]

    def add_mapping(value: Any) -> None:
        mapping = value if isinstance(value, dict) else _json_object(value)
        if not isinstance(mapping, dict):
            return
        session_id = _thread_id_from_mapping(mapping)
        if not session_id:
            return
        engine = mapping.get("engine")
        if engine == "claude":
            chosen_engine: _Runtime | None = "claude"
        elif engine == "codex":
            chosen_engine = "codex"
        else:
            chosen_engine = None
        found.append((chosen_engine, session_id))

    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") in _AGENTS_SERVER_TOOL_NAMES:
                add_mapping(block.get("input"))

    mcp_meta = entry.get("mcpMeta")
    if isinstance(mcp_meta, dict):
        add_mapping(mcp_meta.get("structuredContent"))

    tool_result = entry.get("toolUseResult")
    add_mapping(tool_result)

    payload = entry.get("payload")
    if isinstance(payload, dict) and payload.get("type") in {"custom_tool_call", "custom_tool_call_output"}:
        name = payload.get("name")
        if payload.get("type") == "custom_tool_call" and name in _AGENTS_SERVER_TOOL_NAMES:
            add_mapping(payload.get("arguments") or payload.get("input"))
        if payload.get("type") == "custom_tool_call_output" and payload.get("call_id") in agents_server_call_ids:
            output = payload.get("output")
            add_mapping(output)
            for text in _codex_text_blocks(output):
                add_mapping(text)

    notification_texts: list[str] = []
    if entry.get("type") == "queue-operation":
        notification_texts.extend(_text_blocks(entry.get("content")))
    notification_texts.extend(_text_blocks(content))
    for text in notification_texts:
        for match in _TASK_RESULT_PATTERN.finditer(text):
            add_mapping(match.group(1))
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


def _codex_home(explicit: str | None = None) -> Path:
    """Codexの記録の保存先を、明示引数、空でない`CODEX_HOME`、`~/.codex`の順に解決する。"""
    if explicit:
        return Path(explicit)
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home)
    return Path.home() / ".codex"


def _rollout_candidates(thread_id: str, codex_home: Path) -> list[Path]:
    """Thread IDへ完全suffix一致する`sessions`配下のrolloutをファイル名順で返す。

    backupの写しは`sessions`の外へ保存されるため、この探索範囲では一致しない。
    """
    escaped_thread = re.escape(thread_id)
    return sorted(
        path
        for path in codex_home.glob(f"sessions/*/*/*/rollout-*{thread_id}.jsonl")
        if re.search(rf"rollout-.*{escaped_thread}\.jsonl$", path.name)
    )


def _rollout_path(thread_id: str, codex_home: str | None = None) -> Path | None:
    """Thread IDへ一意に対応するrolloutを返し、0件又は複数件ではNoneを返す。"""
    try:
        return _resolve_codex_transcript(thread_id, codex_home)
    except ValueError:
        return None


def _resolve_codex_transcript(thread_id: str, codex_home: str | None = None) -> Path:
    """Codex thread IDから親transcriptの正本を1件解決する。

    一致が0件又は複数件の場合は証拠不足として例外を送出する。
    """
    base = _codex_home(codex_home)
    candidates = _rollout_candidates(thread_id, base)
    if not candidates:
        raise ValueError(f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが{base / 'sessions'}配下に無い")
    if len(candidates) > 1:
        joined = ", ".join(str(path) for path in candidates)
        raise ValueError(f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが複数ある: {joined}")
    return candidates[0]


def _claude_transcript_path(session_id: str) -> Path | None:
    """Claude Code transcriptのsession_idに対応するJSONLを探す。"""
    projects = Path.home() / ".claude" / "projects"
    candidates = sorted(projects.glob(f"**/{session_id}.jsonl"))
    return candidates[0] if candidates else None


def _session_path(
    engine: _Runtime | None,
    session_id: str,
    codex_home: str | None = None,
) -> tuple[Path, _Runtime] | None:
    """実行系ヒントを優先して両方の記録正本を探索する。"""
    runtimes: tuple[_Runtime, _Runtime]
    runtimes = ("claude", "codex") if engine == "claude" else ("codex", "claude")
    for runtime in runtimes:
        path = _rollout_path(session_id, codex_home) if runtime == "codex" else _claude_transcript_path(session_id)
        if path is not None:
            return path, runtime
    return None


def _subagent_records(source: _CollectedRecord) -> list[_CollectedRecord]:
    """Claude記録に付随するサブエージェント記録をファイル名順で返す。"""
    if source.runtime != "claude":
        return []
    subagent_dir = source.path.with_suffix("") / "subagents"
    try:
        paths = sorted(subagent_dir.glob("agent-*.jsonl"))
    except OSError:
        return []
    selected: list[_CollectedRecord] = []
    for path in paths:
        records = _load_records(str(path))
        if records is None:
            continue
        meta_path = path.with_name(f"{path.stem}.meta.json")
        try:
            raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            raw_meta = {}
        agent_type = raw_meta.get("agentType") if isinstance(raw_meta, dict) else None
        record_id = path.stem if source.record_id == "main" else f"{source.record_id}/{path.stem}"
        selected.append(
            _CollectedRecord(
                record_id,
                path,
                records,
                _detect_runtime([record.entry for record in records]),
                source.record_id,
                None,
                agent_type if isinstance(agent_type, str) else None,
                "subagent",
            )
        )
    return selected


def _collect_records(
    transcript_path: str,
    main_records: list[_Record],
    codex_home: str | None = None,
) -> tuple[list[_CollectedRecord], list[_UnresolvedRecord]]:
    """メイン記録から全ての付随記録と委譲先を発見順に再帰収集する。"""
    main_path = Path(transcript_path)
    collected = [
        _CollectedRecord(
            "main",
            main_path,
            main_records,
            _detect_runtime([record.entry for record in main_records]),
            None,
            None,
            None,
            "main",
        )
    ]
    seen_paths = {main_path.resolve()}
    seen_sessions: set[str] = set()
    unresolved: list[_UnresolvedRecord] = []
    index = 0
    while index < len(collected):
        source = collected[index]
        index += 1
        agents_server_call_ids = _agents_server_call_ids(source.records)
        for subagent in _subagent_records(source):
            resolved = subagent.path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            collected.append(subagent)
        for record in source.records:
            for engine, session_id in _thread_ids_from_record(record, agents_server_call_ids):
                if session_id in seen_sessions:
                    continue
                seen_sessions.add(session_id)
                resolved_session = _session_path(engine, session_id, codex_home)
                if resolved_session is None:
                    unresolved.append(_UnresolvedRecord(session_id, record.line))
                    continue
                path, resolved_engine = resolved_session
                record_id = f"{resolved_engine}:{session_id}"
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                records = _load_records(str(path))
                if records is None:
                    unresolved.append(_UnresolvedRecord(session_id, record.line))
                    continue
                seen_paths.add(resolved)
                collected.append(
                    _CollectedRecord(
                        record_id,
                        path,
                        records,
                        resolved_engine,
                        source.record_id,
                        record.line,
                        None,
                        "session",
                    )
                )
    return collected, unresolved


def _unresolved_events(unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    """解決できなかった委譲先を機械可読イベントへ変換する。"""
    return [{"kind": "unresolved-record", "record": item.record_id, "line": item.line} for item in unresolved]


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


def _claude_compaction_fields(metadata: Any) -> dict[str, Any]:
    """Claude Codeの`compactMetadata`から契機・前後トークン・所要時間を取り出す。

    所要時間はミリ秒で記録されるため秒へ換算する。欄を持たない記録もあるため、
    取得できた欄だけを返す。
    """
    if not isinstance(metadata, dict):
        return {}
    fields: dict[str, Any] = {}
    trigger = metadata.get("trigger")
    if isinstance(trigger, str):
        fields["trigger"] = trigger
    for key, source_key in (("pre_tokens", "preTokens"), ("post_tokens", "postTokens")):
        value = metadata.get(source_key)
        if isinstance(value, int) and not isinstance(value, bool):
            fields[key] = value
    duration_ms = metadata.get("durationMs")
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        fields["duration_seconds"] = round(duration_ms / 1000, 1)
    return fields


def _compaction_event(record: _Record, record_id: str) -> dict[str, Any] | None:
    """コンパクション1回分のイベントを返す。該当しないレコードでは`None`を返す。

    Claude Codeは`subtype`が`compact_boundary`のsystemレコード、Codexは`type`が`compacted`の
    レコードとして1回の発生を記録する。所要時間の欄はClaude Code側だけが持つ。
    """
    entry = record.entry
    entry_type = entry.get("type")
    if entry_type == "system" and entry.get("subtype") == "compact_boundary":
        engine: _Runtime = "claude"
    elif entry_type == "compacted":
        engine = "codex"
    else:
        return None
    event: dict[str, Any] = {"kind": "stats-compaction", "record": record_id, "line": record.line, "engine": engine}
    timestamp = entry.get("timestamp")
    if isinstance(timestamp, str):
        event["timestamp"] = timestamp
    if engine == "claude":
        event.update(_claude_compaction_fields(entry.get("compactMetadata")))
    return event


def _stats_compaction_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """全記録のコンパクションの発生位置と件数を返す。

    メイン記録・サブエージェント記録・委譲先セッションのいずれで発生した分も数える。
    発生が無い場合も件数0の集計イベントだけは返し、発生の有無を呼び出し側が判別できるようにする。
    """
    events = [
        event
        for item in collected
        for record in item.records
        if (event := _compaction_event(record, item.record_id)) is not None
    ]
    events.sort(key=lambda event: (event["record"], event["line"]))
    counts = collections.Counter(event["record"] for event in events)
    total = {
        "kind": "stats-compaction-total",
        "count": len(events),
        "by_record": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "total_duration_seconds": round(sum(event.get("duration_seconds", 0.0) for event in events), 1),
    }
    return [*events, total]


def _stats_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """セッション全体を対象とした集計イベント列を返す。

    `stats-total`はメイン記録・全サブエージェント記録・全Codexスレッドの3区分の合算とする。
    3区分は記録ファイルが互いに排他であり、トークンが重複しない。
    Codex形式の内訳はClaude形式と成分の意味が異なるため、合算前に`_codex_normalized_tokens`で
    4成分へ変換する。メイン記録自体がCodex形式である場合も同じ変換を適用する。
    変換は成分ごとの加減算だけで構成され、`cached_input_tokens`は各レコードで`input_tokens`へ
    内包されるため、レコード単位で変換してから合算した値と、合算してから変換した値は一致する。
    個別表示の`stats-summary`と`stats-codex-thread`はCodexの全成分をそのまま表示する。
    """
    main_record = collected[0]
    main_records = main_record.records
    runtime = main_record.runtime
    if runtime is None:
        return _fallback()
    summary = _stats_summary_data(main_records, runtime)
    subagents = [item for item in collected if item.role == "subagent" and item.runtime == "claude"]
    thread_summaries: list[tuple[_CollectedRecord, _Runtime, dict[str, Any]]] = []
    for item in collected:
        if item.role == "session" and item.runtime is not None:
            thread_summaries.append((item, item.runtime, _stats_summary_data(item.records, item.runtime)))

    total_tokens: dict[str, int] = {}
    if isinstance(summary.get("tokens"), dict):
        _add_tokens(total_tokens, _codex_normalized_tokens(summary["tokens"]) if runtime == "codex" else summary["tokens"])
    for subagent in subagents:
        sub_summary = _stats_summary_data(subagent.records, "claude")
        if isinstance(sub_summary.get("tokens"), dict):
            _add_tokens(total_tokens, sub_summary["tokens"])
    for _, thread_runtime, thread_summary in thread_summaries:
        if isinstance(thread_summary.get("tokens"), dict):
            _add_tokens(
                total_tokens,
                _codex_normalized_tokens(thread_summary["tokens"]) if thread_runtime == "codex" else thread_summary["tokens"],
            )

    thread_counts: dict[str, int] = collections.Counter(thread_runtime for _, thread_runtime, _ in thread_summaries)

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
    events.extend(_stats_compaction_events(collected))

    if subagents:
        subagent_rows: list[tuple[str, str | None, dict[str, Any]]] = []
        subagent_total: dict[str, int] = {key: 0 for key in _CLAUDE_TOKEN_KEYS}
        for subagent in subagents:
            sub_summary = _stats_summary_data(subagent.records, "claude")
            row: dict[str, Any] = {"agent": subagent.record_id, **sub_summary}
            if subagent.agent_type:
                row["agent_type"] = subagent.agent_type
            row["elapsed_seconds"] = sub_summary.get("elapsed_seconds", 0)
            row["tokens"] = sub_summary.get("tokens", {key: 0 for key in _CLAUDE_TOKEN_KEYS})
            row["api_messages"] = sub_summary.get("api_messages", 0)
            subagent_rows.append((subagent.record_id, subagent.agent_type, row))
            _add_tokens(subagent_total, row["tokens"])
        for _, _, row in sorted(subagent_rows, key=lambda item: (-_token_total(item[2]["tokens"]), item[0])):
            events.append({"kind": "stats-subagent", **row})
        events.append(
            {
                "kind": "stats-subagent-total",
                "count": len(subagents),
                "tokens": subagent_total,
            }
        )

    for thread, thread_runtime, thread_summary in sorted(
        thread_summaries,
        key=lambda item: (-item[2].get("tokens", {}).get("total_tokens", 0), item[0].record_id),
    ):
        # `line`はメイン記録の`--detail`用であるため、メイン以外から見つけた委譲先では
        # 由来記録IDを`agent`へ付ける。
        thread_event: dict[str, Any] = {
            "kind": "stats-agent-thread",
            "engine": thread_runtime,
            "session_id": thread.record_id.split(":", 1)[1],
            "thread": thread.record_id.split(":", 1)[1],
            **thread_summary,
        }
        if thread.source_record != "main":
            thread_event["agent"] = thread.source_record
        elif thread.source_line is not None:
            thread_event["line"] = thread.source_line
        events.append(thread_event)
    return events


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
    """セッション全体の実行時警告を行番号付きで返す。

    同じhook通知は成功記録と追加コンテキストへ重複して格納されるため、
    ツール呼び出し識別子と本文の組で1件として扱う。
    識別子を持たない警告はコマンド出力由来の検出を失わないように個別に保持する。
    一致しない場合はその事実を返す。
    """
    events: list[dict[str, Any]] = []
    seen_hook_warnings: set[tuple[str, str]] = set()
    scannable = _scannable_records(records)
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
    return events


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


def _events_with_record(events: list[dict[str, Any]], record_id: str) -> list[dict[str, Any]]:
    """イベントを複製し、由来記録IDを付ける。"""
    return [{**event, "record": record_id} for event in events]


def _default_events(collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        events.extend(_events_with_record(_extract_records(item.records), item.record_id))
    events.extend(_unresolved_events(unresolved))
    return events


def _warning_collection_events(collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        events.extend(_events_with_record(_warning_events(item.records), item.record_id))
    if not events:
        events.append({"kind": "warning", "text": "一致なし"})
    events.extend(_unresolved_events(unresolved))
    return events


def _grep_collection_events(
    collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord], pattern: re.Pattern[str]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    total = 0
    for item in collected:
        record_events = _grep_events(item.records, pattern)
        record_count = record_events[-1]["count"]
        total += record_count
        events.extend(_events_with_record(record_events[:-1], item.record_id))
        if record_count:
            events.extend(_events_with_record(record_events[-1:], item.record_id))
    events.append({"kind": "summary", "count": total})
    events.extend(_unresolved_events(unresolved))
    return events


def _detail_collection_events(collected: list[_CollectedRecord], locators: list[str]) -> tuple[list[dict[str, Any]], int]:
    by_id = {item.record_id: item for item in collected}
    events: list[dict[str, Any]] = []
    for locator in locators:
        if ":" in locator:
            record_id, raw_line = locator.rsplit(":", 1)
        else:
            record_id, raw_line = "main", locator
        if not record_id or not raw_line.isdecimal():
            return [{"kind": "error", "text": f"詳細位置が不正: {locator}"}], 2
        selected = by_id.get(record_id)
        if selected is None:
            return [{"kind": "error", "text": f"記録が不明: {record_id}"}], 2
        record_events, exit_code = _detail_events(selected.records, [int(raw_line)])
        if exit_code:
            return record_events, exit_code
        events.extend(_events_with_record(record_events, record_id))
    return events, 0


_BUNDLE_SCAN_FILENAMES = ("timeline.jsonl", "warnings.jsonl", "stats.jsonl", "hook-notices.jsonl")
_BUNDLE_BODY_KINDS = frozenset({"failed-tool", "agent-completion", "final-result"})
_BUNDLE_LOCATOR_ONLY_KINDS = frozenset({"user"})
_BUNDLE_BODY_LENGTH = 200
_BUNDLE_WARNING_GROUP_LENGTH = 120
_BUNDLE_WARNING_SAMPLE_COUNT = 3


def _bundle_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    directory: Path,
) -> tuple[list[dict[str, Any]], int]:
    """4走査を1回の記録読み込みで行い、走査ごとの全量をファイルへ書いて要約だけを返す。

    標準出力へ返す要約の項目は、抽出担当が走査ごとの全量をファイルへ保存し、自作の集計コマンドで
    再加工していた工程を代替する目的で設けた。項目を減らすと当該工程が抽出担当側へ戻るため、
    取捨は代替対象の集計を確認してから判断する。
    未解決記録のイベントは標準出力へ1回だけ書く。各ファイルの内容は、当該走査を単独で実行した
    出力から未解決記録のイベントを除いたものと一致する。
    """
    if not directory.is_dir():
        return [{"kind": "error", "text": f"出力先が実在するディレクトリでない: {directory}"}], 2
    resolved = directory.resolve()
    timeline = _default_events(collected, [])
    warnings = _warning_collection_events(collected, [])
    stats = _stats_events(collected)
    hook_notices = _hook_notice_events([record for item in collected for record in item.records])

    events: list[dict[str, Any]] = []
    for filename, scan_events in zip(_BUNDLE_SCAN_FILENAMES, (timeline, warnings, stats, hook_notices), strict=True):
        path = resolved / filename
        path.write_text(
            "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in scan_events),
            encoding="utf-8",
        )
        events.append({"kind": "bundle-file", "path": str(path), "count": len(scan_events)})
    events.extend(_bundle_timeline_events(timeline))
    events.extend(_bundle_warning_events(warnings))
    events.extend(stats)
    events.extend(hook_notices)
    events.extend(_unresolved_events(unresolved))
    return events, 0


def _bundle_timeline_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """通常表示を、イベント種別ごとの件数と問題候補の位置へ要約する。

    `assistant`と`skill-invocation`は件数だけで候補を確定できるため、本文を標準出力へ含めない。
    位置を伴う種別の本文も冒頭に限り、全体は`--detail`で取得する。
    """
    counts = collections.Counter(str(event["kind"]) for event in timeline)
    events: list[dict[str, Any]] = [
        {"kind": "bundle-kind-count", "event_kind": event_kind, "count": count}
        for event_kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    for event in timeline:
        event_kind = event["kind"]
        if event_kind in _BUNDLE_BODY_KINDS:
            events.append(
                {
                    "kind": "bundle-locator",
                    "event_kind": event_kind,
                    "record": event["record"],
                    "line": event["line"],
                    "text": _clip(str(event.get("text", "")), _BUNDLE_BODY_LENGTH),
                }
            )
        elif event_kind in _BUNDLE_LOCATOR_ONLY_KINDS:
            events.append(
                {"kind": "bundle-locator", "event_kind": event_kind, "record": event["record"], "line": event["line"]}
            )
    return events


def _bundle_warning_events(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """警告を本文の冒頭で分類し、分類ごとの件数と先頭の代表位置へ要約する。

    一致が無い場合に`_warning_collection_events`が返す位置を持たないイベントは分類の対象外とする。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in warnings:
        if event.get("kind") != "warning" or "line" not in event:
            continue
        groups.setdefault(str(event.get("text", ""))[:_BUNDLE_WARNING_GROUP_LENGTH], []).append(event)
    return [
        {
            "kind": "bundle-warning-group",
            "text": text,
            "count": len(items),
            "samples": [{"record": item["record"], "line": item["line"]} for item in items[:_BUNDLE_WARNING_SAMPLE_COUNT]],
        }
        for text, items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def _build_parser() -> argparse.ArgumentParser:
    """既定の抽出と照会モードの引数を定義する。"""
    parser = argparse.ArgumentParser(
        description=(
            "transcriptから振り返り用の時系列証拠を抽出・照会する。--statsは経過時間、トークン消費、"
            "ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を返す。"
            "メイン記録・補助記録ともセッション全体を対象とする。"
            "stats-toolの合計秒は並列実行分を含むため壁時計時間とは一致しない。" + _CLAUDE_ONLY_NOTE
        )
    )
    parser.add_argument(
        "transcript_path",
        nargs="?",
        help="transcriptの絶対パス。読み込み失敗時はエラーイベントを出力して終了コード2を返す。`--codex-thread-id`と併用しない。",
    )
    parser.add_argument(
        "--codex-thread-id",
        metavar="THREAD_ID",
        help="Codex thread IDから親transcriptの正本を解決して抽出を開始する。"
        "保存先は`--codex-home`、空でない`CODEX_HOME`、`~/.codex`の順に解決し、"
        "`sessions`配下で完全suffix一致するrolloutが1件でない場合はエラーイベントを出力して終了コード2を返す。",
    )
    parser.add_argument(
        "--codex-home",
        metavar="DIR",
        help="Codexの記録の保存先。`--codex-thread-id`と併用する。",
    )
    parser.add_argument(
        "--observation-boundary",
        metavar="TIMESTAMP",
        help="ISO 8601の時刻を観測境界とし、親記録のうち当該時刻より後の`timestamp`を持つレコードを"
        "全モードの対象外にする。委譲先の記録へは適用しない。`--detail`の行番号は元ファイルの行番号を維持する。"
        "解析できない値はエラーイベントを出力して終了コード2を返す。",
    )
    parser.add_argument(
        "--warn",
        action="store_true",
        help="セッション全体のエントリから、行頭の警告マーカーまたは"
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
        action="append",
        default=None,
        metavar="RECORD:LINE",
        help="指定した<記録>:<行番号>（数値だけならメイン記録）のエントリの詳細（tool_useの入力全体・tool_result本文。"
        "本文が退避されている場合はツール実行結果側の本文）を照会する。複数指定ではオプションを繰り返す。"
        "出力量の上限で本文を省略したエントリのイベントには`omitted`を付ける。",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="経過時間、トークン消費、ツール別・呼び出し別・サブエージェント別・Codexスレッド別の集計を照会する。"
        "コンパクションの発生位置と回数は`stats-compaction`と`stats-compaction-total`が返す。" + _CLAUDE_ONLY_NOTE,
    )
    parser.add_argument(
        "--hook-notices",
        action="store_true",
        help="hook実行の記録（追加コンテキスト・システムメッセージ・遮断エラー・実行成功）に"
        "格納された通知本文だけを集計し、hook識別子・発動元・タグ・種別ごとの件数と"
        "重複を除いた通知件数を照会する。" + _CLAUDE_ONLY_NOTE,
    )
    parser.add_argument(
        "--bundle",
        metavar="DIR",
        help="通常表示、`--warn`、`--stats`及び`--hook-notices`の走査を1回の記録読み込みで行い、"
        "走査ごとの全量を指定したディレクトリ配下のファイルへ書く。"
        "標準出力へは、走査ごとのファイルの絶対パスとイベント件数、通常表示のイベント種別ごとの件数、"
        "問題候補の特定に用いるイベントの位置と本文の冒頭、警告の種別ごとの件数、集計済みの走査の全量を返す。"
        "指定するディレクトリは実在していることを要する。他の照会オプションとは併用しない。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """証拠または照会結果を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if (
        sum((args.warn, args.grep is not None, args.detail is not None, args.stats, args.hook_notices, args.bundle is not None))
        > 1
    ):
        return _print_error("--warn・--grep・--detail・--stats・--hook-notices・--bundleは併用できない")

    if (args.transcript_path is None) == (args.codex_thread_id is None):
        return _print_error("transcript_pathと--codex-thread-idはいずれか一方だけを指定する")
    if args.codex_thread_id is not None:
        try:
            transcript_path = str(_resolve_codex_transcript(args.codex_thread_id, args.codex_home))
        except ValueError as error:
            return _print_error(str(error))
    else:
        transcript_path = args.transcript_path

    records = _load_records(transcript_path)
    if records is None:
        return _print_error(f"対象記録を読み込めない: {transcript_path}")
    if args.observation_boundary is not None:
        try:
            boundary = _parse_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}")
        records = _apply_observation_boundary(records, boundary)
    delegate_codex_home = args.codex_home if args.codex_thread_id is not None else None
    collected, unresolved = _collect_records(transcript_path, records, delegate_codex_home)

    if args.bundle is not None:
        events, exit_code = _bundle_events(collected, unresolved, Path(args.bundle))
        _print_events(events)
        return exit_code
    if args.warn:
        _print_events(_warning_collection_events(collected, unresolved))
        return 0
    if args.grep is not None:
        try:
            pattern = re.compile(args.grep)
        except re.error as error:
            return _print_error(f"正規表現が不正: {error}")
        _print_events(_grep_collection_events(collected, unresolved, pattern))
        return 0
    if args.detail is not None:
        events, exit_code = _detail_collection_events(collected, args.detail)
        _print_events(events)
        return exit_code
    if args.stats:
        _print_events([*_stats_events(collected), *_unresolved_events(unresolved)])
        return 0
    if args.hook_notices:
        _print_events(
            [*_hook_notice_events([record for item in collected for record in item.records]), *_unresolved_events(unresolved)]
        )
        return 0

    _print_events(_default_events(collected, unresolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
