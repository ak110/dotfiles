"""振り返りの証拠抽出の本体。Claude Code・Codex・Antigravityの記録を時系列イベントへ変換し、照会モードが共有する値と処理を持つ。

`session_review_evidence.py`（`atk run-script session-review-evidence`の起動スクリプト）と同じディレクトリに置き、
各照会モードのモジュールが本モジュールの記録の読込、イベントの組み立て、本文の切り詰めと自身の起動の判定を使う。
"""

from __future__ import annotations

import contextvars
import datetime
import json
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NamedTuple

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common import runtime_inserted as _runtime_inserted
from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.runtime_inserted import IMPROVEMENT_MARKER as _IMPROVEMENT_MARKER
from agent_toolkit._common.runtime_inserted import is_runtime_generated as _is_runtime_generated
from agent_toolkit._common.runtime_inserted import is_runtime_inserted_text as _is_runtime_inserted_text

_MAX_TEXT_LENGTH = 2000


_OMISSION_MARK = "…[省略]"


# Claude Codeのサブエージェントが報告本文を委譲元へ渡すツールの名前。
_HANDBACK_TOOL = "SubagentHandback"


_LINE_NUMBER_PREFIX = re.compile(r"^\s*\d+\t(.*)$")


_SKILL_INVOCATION_PREFIX = "Base directory for this skill: "


_SELF_SCRIPT_STEM = "session_review_evidence"


_SEGMENT_SEPARATORS = re.compile(r"[;&|]+")


_PATH_SEPARATORS = re.compile(r"[/\\]")


_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


_SHELL_NAMES = frozenset({"sh", "bash", "zsh"})


_SCRIPT_RUNNERS = frozenset({"uv", "uvx", "env"})


_HOOK_RECORD_TYPES = frozenset({"hook_additional_context", "hook_system_message", "hook_blocking_error", "hook_success"})


def _is_hook_record(value: dict[str, Any]) -> bool:
    """hook実行の記録に当たるかを`type`の値で判定する。

    `type`の値は文字列とは限らない。ツール定義を含む記録では
    `attachment.tools[].schema.input_schema.properties`配下に`type`という名前の
    プロパティ定義が現れ、その値はJSON Schemaのdictになる。
    集合に含まれるか判定する前に文字列であることを確かめないと`TypeError`で走査が止まる。
    """
    return isinstance(value.get("type"), str) and value["type"] in _HOOK_RECORD_TYPES


_HOOK_NOTICE_MARKER = re.compile(
    rf"(?:<(?:{_message_format.AUTO_ELEMENT_NAME_PATTERN})"
    r'(?=[^>]*\ssource="(?P<hook_xml>[^"]+)")(?=[^>]*\skind="(?P<tag_xml>[^"]+)")[^>]*>|'
    r"\[auto-generated:\s*(?P<hook_legacy>[^\]]*?)\s*\](?:\s*\[(?P<tag_legacy>[^\]]*)\])?)"
)


# 本文の可変部（語頭から始まるパス、session識別子などのUUID、UWI識別子・行番号・トークン数などの数値）。
# 種別キーの分裂を防ぐため置換する。UUIDは英字を含み数値の置換だけでは1件ごとに別の種別へ分かれるため、数値より先に置換する。
# パスは語頭に限定するが、数値列は語頭・語中を問わず置換するため、`github.com/ak110/dotfiles`のような
# 固定の識別子も数値部分が置換される。
_CANDIDATE_VARIABLE = re.compile(
    r"""(?<![^\s(\[<'"`])~?/[^\s`'"]+|\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b|\d+"""
)


_CANDIDATE_VARIABLE_PLACEHOLDER = "<var>"


_HOOK_FAILURE_PREFIX = re.compile(r"^[^\r\n]*?\bhook error:\s*\[[^\r\n]*?\]:\s*", re.IGNORECASE)


_FALLBACK_TEXT = (
    "記録は読み込めたが形式を判定できないため抽出証拠を生成できない。"
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


_Runtime = Literal["claude", "codex", "agy"]


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
    aliases: tuple[str, ...] = ()


class _UnresolvedRecord(NamedTuple):
    """解決できない委譲または委譲先記録を機械可読イベントへ渡す。"""

    record_id: str
    line: int
    kind: Literal["unresolved-record", "unresolved-delegation"] = "unresolved-record"


_TEXT_LIMIT: contextvars.ContextVar[int | None] = contextvars.ContextVar("_TEXT_LIMIT", default=_MAX_TEXT_LENGTH)
"""`_clip`が上限を省略された場合に使う本文の上限。`None`は切り詰めないことを表す。

会話の流れは発話の全文から先頭と末尾を切り出すため、イベントの生成を共有しつつ上限だけを外す。
"""


def _clip(text: str, limit: int | None = None) -> str:
    """証拠の意味を保ったまま巨大な本文を制限する。"""
    normalized = text.strip()
    effective = _TEXT_LIMIT.get() if limit is None else limit
    if effective is None or len(normalized) <= effective:
        return normalized
    return normalized[:effective] + _OMISSION_MARK


def _clip_assistant_text(text: str) -> str:
    """本文の先頭を短縮し、省略区間にある改善点の標識行は全文で保持する。"""
    normalized = text.strip()
    clipped = _clip(normalized)
    if clipped == normalized:
        return clipped
    boundary = len(clipped) - len(_OMISSION_MARK)
    prefix = normalized[:boundary]
    kept: list[str] = []
    start = 0
    for line in normalized.splitlines(keepends=True):
        end = start + len(line)
        if line.lstrip().startswith(_IMPROVEMENT_MARKER) and end > boundary:
            if start < boundary:
                prefix = normalized[:start]
            kept.append(line.rstrip("\r\n"))
        start = end
    if not kept:
        return clipped
    return prefix + _OMISSION_MARK + "\n" + "\n".join(kept)


class _DetailBudget:
    """1エントリの詳細出力が共有する残り文字数と、省略の発生有無。

    詳細は`--detail`の指定行ごとに複数の文字列へ分かれるため、上限を文字列単位で適用すると
    1エントリの出力量が指定上限を超える。残り予算を出現順に配分して本文の合計を上限内へ収める。
    省略標識も返す文字数として予算から差し引くため、文字列値の個数が増えても合計は上限を超えない。
    予算が標識の長さに満たない時点以降の本文は空文字列となり、本文が元から空である場合と
    文字列単体では区別できない。この区別のため、省略が1回でも生じたかを`omitted`が保持し、
    呼び出し側がそのエントリのイベントへ標識として付ける。
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
    """共通イベントを生成し、アシスタント本文の省略区間の改善点を候補判定まで保つ。"""
    runtime_inserted = kind == "user" and _is_runtime_inserted_text(text)
    clipped = _clip_assistant_text(text) if kind == "assistant" else _clip(text)
    if not clipped:
        return None
    event: dict[str, Any] = {"kind": kind, "text": clipped}
    if kind == "assistant":
        improvements = _runtime_inserted.reported_improvement_lines(text)
        if improvements:
            event["reported_improvements"] = improvements
    if kind == "user":
        event["runtime_inserted"] = runtime_inserted
    if tool:
        event["tool"] = tool
    return event


def _generated_user_event(event: dict[str, Any]) -> bool:
    """`_event`が原文から付けた由来の標識で、実行環境が生成したユーザーイベントかを返す。"""
    return event.get("runtime_generated") is True or event.get("runtime_inserted") is True


_OfferedOption = tuple[str, str]
"""確認で提示した選択肢1件のlabelとdescription。descriptionを持たない選択肢は空文字列とする。"""


_CodexPendingQuestions = dict[str, tuple[int, dict[str, tuple[str, list[_OfferedOption]]]]]
"""Codexの`request_user_input`のcall_idごとの、質問側の行番号と質問IDごとの質問文・提示した選択肢。"""


class _AnsweredQuestion(NamedTuple):
    """回答イベントへ書く1問分の質問文、提示した選択肢、回答および自由記述。"""

    question: str
    options: list[_OfferedOption]
    answers: list[str]
    notes: str


def _question_answers_event(pairs: list[_AnsweredQuestion]) -> dict[str, Any] | None:
    """質問側の文面とユーザーが入力した値を由来別に分けたuserイベントへ変換する。

    選択肢のlabelと一致しない回答と、自由記述を伴う回答は従来の判断を是正した介入であるため、
    `answer_intervention`を付けて問題候補の母集団へ残す。Claude CodeとCodexの両方の回答がこの関数を通るため、
    介入の判定をここへ置き、どちらの回答でも是正を含む回答を同じ条件で候補に残す。
    """
    user_text: list[str] = []
    assistant_context: list[dict[str, Any]] = []
    user_response: list[dict[str, Any]] = []
    intervention = False
    for pair in pairs:
        labels = [label for label, _description in pair.options]
        if pair.notes or not all(_is_offered_answer(answer, labels) for answer in pair.answers):
            intervention = True
        answers = [_clip(answer) for answer in pair.answers if _clip(answer)]
        notes = _clip(pair.notes) if pair.notes else ""
        user_text.extend(answers)
        if notes:
            user_text.append(notes)
        assistant_context.append(
            {
                "question": _clip(pair.question),
                "options": [{"label": _clip(label), "description": _clip(description)} for label, description in pair.options],
            }
        )
        response: dict[str, Any] = {"answers": answers}
        if pair.notes:
            response["notes"] = notes
        user_response.append(response)
    event = _event("user", "\n".join(user_text))
    if event is not None:
        event["assistant_context"] = assistant_context
        event["user_response"] = user_response
        if intervention:
            event["answer_intervention"] = True
    return event


class _PendingQuestion(NamedTuple):
    """回答イベントの生成に要する、質問側の行番号と質問文ごとの提示した選択肢。"""

    line: int
    options: dict[str, list[_OfferedOption]]


def _claude_question_options(content: Any) -> dict[str, dict[str, list[_OfferedOption]]]:
    """AskUserQuestionのtool_use IDごとに、質問文と提示した選択肢の対応を取得する。

    labelは`input.questions[].options[].label`に現れる。回答の文字列をこのlabelの集合と
    比べて、選択肢をそのまま選んだ回答と方針を是正した回答を判別する。
    """
    if not isinstance(content, list):
        return {}
    options: dict[str, dict[str, list[_OfferedOption]]] = {}
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") != "AskUserQuestion" or not isinstance(block.get("id"), str):
            continue
        options[block["id"]] = _claude_offered_options(block.get("input"))
    return options


def _claude_offered_options(payload: Any) -> dict[str, list[_OfferedOption]]:
    """AskUserQuestionの入力から、質問文ごとに提示した選択肢のlabelとdescriptionを取得する。"""
    if not isinstance(payload, dict):
        return {}
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return {}
    options: dict[str, list[_OfferedOption]] = {}
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("question"), str):
            continue
        if not isinstance(question.get("options"), list):
            continue
        options[question["question"]] = _offered_options(question["options"])
    return options


def _offered_options(choices: Any) -> list[_OfferedOption]:
    """質問の`options`配列から、labelを持つ選択肢のlabelとdescriptionを取得する。

    Claude Codeの`AskUserQuestion`とCodexの`request_user_input`は同じ形の`options`配列を持つ。
    """
    if not isinstance(choices, list):
        return []
    return [
        (choice["label"], choice["description"] if isinstance(choice.get("description"), str) else "")
        for choice in choices
        if isinstance(choice, dict) and isinstance(choice.get("label"), str)
    ]


def _is_offered_answer(answer: str, labels: list[str]) -> bool:
    """回答の文字列が、提示した選択肢のlabelだけで構成されるかを返す。

    複数選択の回答はlabelをカンマと空白で連結した1つの文字列として記録されるため、
    区切った全ての要素がlabelに一致する場合だけ、選択肢をそのまま選んだ回答とする。
    labelを取得できない記録ではこの判別が成り立たないため、選択肢をそのまま選んだ回答として扱う。
    """
    if not labels:
        return True
    return all(part.strip() in labels for part in answer.split(",") if part.strip())


def _claude_answers_event(
    result: Any,
    content: Any,
    pending_questions: dict[str, _PendingQuestion],
) -> dict[str, Any] | None:
    """対応するAskUserQuestionの結果だけを回答イベントへ変換する。

    質問と回答は別の行に由来するため、行番号には質問側（先頭行）の値を用いる。
    `annotations`の`notes`はユーザーが選択肢の外へ書いた自由記述であり、`preview`は取り込まない。
    介入の標識は`_question_answers_event`が付ける。
    """
    if not isinstance(content, list):
        return None
    result_ids = {
        block["tool_use_id"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
    }
    matched_ids = set(pending_questions).intersection(result_ids)
    if not matched_ids:
        return None
    matched = [pending_questions.pop(matched_id) for matched_id in matched_ids]
    question_line = min(pending.line for pending in matched)
    options: dict[str, list[_OfferedOption]] = {}
    for pending in matched:
        options.update(pending.options)
    if not isinstance(result, dict):
        return None
    answers = result.get("answers")
    if not isinstance(answers, dict) or not all(
        isinstance(question, str) and isinstance(answer, str) for question, answer in answers.items()
    ):
        return None
    annotations = result.get("annotations")
    pairs: list[_AnsweredQuestion] = []
    for question, answer in answers.items():
        annotation = annotations.get(question) if isinstance(annotations, dict) else None
        raw_notes = annotation.get("notes") if isinstance(annotation, dict) else ""
        notes = raw_notes if isinstance(raw_notes, str) else ""
        pairs.append(_AnsweredQuestion(question, options.get(question, []), [answer], notes))
    event = _question_answers_event(pairs)
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
            event["diagnostic_last_line"] = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
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
            # ユーザーへ届いた本文として、`text`ブロックに加えて`send_to_user`の呼び出しの`message`も出来事にする。
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


def _codex_agent_message(payload: dict[str, Any]) -> str:
    """agent_messageの文字列互換を保ち、contentのblock配列を結合する。"""
    for key in ("message", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return "\n".join(_codex_text_blocks(payload.get("content")))


def _json_object(raw: Any) -> dict[str, Any] | None:
    """JSON文字列がobjectなら返し、破損または別の値なら`None`を返す。"""
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _codex_question_call(
    payload: dict[str, Any],
) -> tuple[str, dict[str, tuple[str, list[_OfferedOption]]]] | None:
    """request_user_inputからcall_idごとの質問IDと、質問文・提示した選択肢を取得する。"""
    if payload.get("name") != "request_user_input":
        return None
    call_id = payload.get("call_id")
    arguments = _json_object(payload.get("arguments"))
    if not isinstance(call_id, str) or arguments is None:
        return None
    raw_questions = arguments.get("questions")
    if not isinstance(raw_questions, list):
        return None
    questions: dict[str, tuple[str, list[_OfferedOption]]] = {}
    for raw_question in raw_questions:
        if not isinstance(raw_question, dict):
            continue
        question_id = raw_question.get("id")
        question = raw_question.get("question")
        if isinstance(question_id, str) and isinstance(question, str):
            questions.setdefault(question_id, (question, _offered_options(raw_question.get("options"))))
    return (call_id, questions) if questions else None


def _codex_question_output_event(
    payload: dict[str, Any],
    pending_questions: _CodexPendingQuestions,
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
    pairs: list[_AnsweredQuestion] = []
    for question_id, (question, offered) in questions.items():
        answer_data = raw_answers.get(question_id)
        if not isinstance(answer_data, dict):
            continue
        answers = answer_data.get("answers")
        if not isinstance(answers, list) or not all(isinstance(answer, str) for answer in answers):
            continue
        pairs.append(_AnsweredQuestion(question, offered, answers, ""))
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
    pending_questions: _CodexPendingQuestions = {}
    for line, entry in zip(lines, entries, strict=True):
        for event in _codex_entry_events(entry, line, pending_questions):
            event.setdefault("line", line)
            _set_entry_timestamp(event, entry)
            events.append(event)
    return events


def _codex_entry_events(
    entry: dict[str, Any],
    line: int,
    pending_questions: _CodexPendingQuestions,
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
                    if kind == "user" and _is_runtime_generated(entry):
                        event["runtime_generated"] = True
                    phase = payload.get("phase")
                    if isinstance(phase, str):
                        event["phase"] = phase
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


# Antigravity（agy）の委譲先ログは、agents_serverが公開JSONイベントを1行ずつ保存したものである。
_AGY_EVENT_TYPES = frozenset({"init", "step_update", "result"})


def _extract_agy(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """Antigravityの委譲先ログを共通イベントへ変換する。

    ログは時刻を持たないため、各イベントの`timestamp`はnullになる。
    委譲先がさらに起動した孫（`invoke_subagent`・`call_mcp_tool`など）は、ユーザーが網羅の対象から外したため
    失敗した場合を除いて事象へ写さず、委譲先の発見元にもしない。
    """
    events: list[dict[str, Any]] = []
    for line, entry in zip(lines, entries, strict=True):
        for event in _agy_entry_events(entry):
            event.setdefault("line", line)
            _set_entry_timestamp(event, entry)
            events.append(event)
    return events


def _agy_entry_events(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """agyの1イベントから、ツール失敗・エラー報告・失敗終端・返却本文の共通イベントを取得する。"""
    event_type = entry.get("event")
    body = entry.get(event_type) if isinstance(event_type, str) else None
    if not isinstance(body, dict):
        return []
    events: list[dict[str, Any]] = []
    if event_type == "step_update":
        step_type = body.get("step_type")
        if step_type == "error_message":
            # agyはエラー報告のステップへ本文を持たせないため、ステップの位置だけを示す。
            event = _event(
                "failed-tool", f"agyの委譲先がエラーを報告した（step_index: {body.get('step_index')}）", tool="error_message"
            )
            if event:
                event["tool_name"] = "error_message"
                events.append(event)
        elif step_type == "tool" and body.get("state") != "ACTIVE":
            tool_info = body.get("tool_info")
            error = tool_info.get("error") if isinstance(tool_info, dict) else None
            # 終了したツールステップは、`ERROR`状態に加えて`DONE`状態でも`tool_info.error`で失敗を表す。
            if body.get("state") == "ERROR" or error is not None:
                message = error.get("message") if isinstance(error, dict) else None
                if isinstance(message, str) and message.strip():
                    text = message
                elif error is not None:
                    text = json.dumps(error, ensure_ascii=False)
                else:
                    text = "tool step failed"
                tool_name = str(body.get("tool_name") or "")
                event = _event("failed-tool", text, tool=tool_name)
                if event:
                    event["tool_name"] = tool_name
                    parameters = tool_info.get("parameters") if isinstance(tool_info, dict) else None
                    event["operation"] = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
                    events.append(event)
    elif event_type == "result":
        status = body.get("status")
        if status != "SUCCESS":
            error = body.get("error")
            detail = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False) if error is not None else ""
            event = _event("failed-tool", f"status: {status}\n{detail}".strip(), tool="result")
            if event:
                event["tool_name"] = "result"
                events.append(event)
        response = body.get("response")
        if isinstance(response, str):
            event = _event("assistant", response)
            if event:
                events.append(event)
    return events


RUNTIME_EXTRACTORS: dict[str, Callable[[list[dict[str, Any]], list[int]], list[dict[str, Any]]]] = {
    "claude": _extract_claude,
    "codex": _extract_codex,
    "agy": _extract_agy,
}
"""実行系ごとの時系列への変換。キーの集合は本スクリプトが変換できる実行系を表す。"""


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
        text = error if isinstance(error, str) and error.strip() else "CommandExecution failed"
    event = _event("failed-tool", text, tool="CommandExecution")
    if event:
        command = item.get("command")
        if isinstance(command, list) and all(isinstance(part, str) for part in command):
            event["command"] = _clip(json.dumps(command, ensure_ascii=False))
            event["command_full"] = json.dumps(command, ensure_ascii=False)
            event["executable"] = _basename(command[0]) if command else ""
        event["diagnostic"] = "" if text == "CommandExecution failed" else _clip(text)
        event["diagnostic_last_line"] = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")
        exit_code = item.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            event["exit_code"] = exit_code
    return event


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


def _finalize(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1turnの最終結果への置換と連番付けを行う。

    Claude Codeのサブエージェントは報告本文を`SubagentHandback`の引数で渡し、その後に定型文だけを書く。
    同呼び出しを持つturnでは、最後の同呼び出しの本文を最終結果とする。
    """
    handbacks = [event for event in events if event.get("handback")]
    if handbacks:
        handbacks[-1]["kind"] = "final-result"
    else:
        for event in reversed(events):
            if event["kind"] == "assistant" and event.get("phase") != "commentary":
                event["kind"] = "final-result"
                break
    for event in events:
        event.pop("handback", None)
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
    numbers = lines if lines is not None else list(range(1, len(entries) + 1))
    events: list[dict[str, Any]] = []
    start = 0
    for index, entry in enumerate(entries):
        if _turn_finished(entry, runtime):
            events.extend(_finalize(_extract_for_runtime(entries[start : index + 1], runtime, numbers[start : index + 1])))
            start = index + 1
    if start < len(entries):
        events.extend(_finalize(_extract_for_runtime(entries[start:], runtime, numbers[start:])))
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def _turn_finished(entry: dict[str, Any], runtime: _Runtime) -> bool:
    """既存のホストの終端記録を使い、後続turnの作業を先行返却の分類から切り離す。"""
    if runtime == "codex":
        payload = entry.get("payload")
        return entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "task_complete"
    if runtime == "claude":
        message = entry.get("message")
        return entry.get("type") == "assistant" and isinstance(message, dict) and message.get("stop_reason") == "end_turn"
    return entry.get("event") == "result"


def _detect_runtime(entries: list[dict[str, Any]]) -> _Runtime | None:
    """transcriptのエントリ形式から手動構文を解釈する実行系を返す。"""
    if entries and all(entry.get("event") in _AGY_EVENT_TYPES for entry in entries):
        return "agy"
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
    return RUNTIME_EXTRACTORS[runtime](entries, numbers)


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


def _parse_timestamp(value: str) -> datetime.datetime:
    """記録の`timestamp`などの内部データのISO 8601の時刻を解析し、タイムゾーン無しの値をUTCとして返す。

    ホストは記録の時刻を`Z`付きのUTCで書くため、タイムゾーン無しの値もUTCとみなす。
    CLI引数で受け取る時刻は`_parse_cli_timestamp`で解析する。
    """
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


def _started_after_boundary(records: list[_Record], boundary: datetime.datetime) -> bool:
    """記録の最初の時刻が観測境界より後かを返す。"""
    timestamps = [_record_timestamp(record) for record in records]
    known = [timestamp for timestamp in timestamps if timestamp is not None]
    return bool(known) and min(known) > boundary


def _unresolved_events(unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    """解決できなかった委譲先を機械可読イベントへ変換する。"""
    return [{"kind": item.kind, "record": item.record_id, "line": item.line} for item in unresolved]


def _role_document(item: _CollectedRecord) -> str | None:
    """最初の配送本文の役割文書を読み、圧縮後の再掲で起動時の役割を上書きしない。"""
    for record in item.records:
        message = record.entry.get("message")
        payload = record.entry.get("payload")
        texts: list[str] = []
        if record.entry.get("type") == "user" and isinstance(message, dict):
            texts = _text_blocks(message.get("content"))
        elif isinstance(payload, dict) and payload.get("type") == "message" and payload.get("role") == "user":
            texts = _codex_text_blocks(payload.get("content"))
        for text in texts:
            found = re.search(r"(?m)^次の文書の手順を実行せよ（出所: [^\n]*[/\\]([^/\\\n]+)\.subagent\.md）。", text)
            if found:
                return found[1]
        if texts and not _is_runtime_generated(record.entry) and not all(_is_runtime_inserted_text(text) for text in texts):
            return None
    return None


def _launch_mode(item: _CollectedRecord, collected: list[_CollectedRecord]) -> str | None:
    """起動結果と対応する親の呼び出し入力からmodeを得る。"""
    if item.role == "subagent":
        return item.agent_type
    parent = next((source for source in collected if source.record_id == item.source_record), None)
    if parent is None or item.source_line is None:
        return None
    source = next((record.entry for record in parent.records if record.line == item.source_line), {})
    payload = source.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("item"), dict):
        call = payload["item"]
        arguments = call.get("arguments")
        if isinstance(arguments, dict):
            return _agents_server_tool_names.start_mode(str(call.get("tool", "")), arguments)
    ids: set[str] = set()
    message = source.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        ids = {
            str(block["tool_use_id"]) for block in message["content"] if isinstance(block, dict) and block.get("tool_use_id")
        }
    if isinstance(payload, dict) and payload.get("call_id"):
        ids.add(str(payload["call_id"]))
    for record in parent.records:
        message = record.entry.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            for block in message["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") in ids:
                    inputs = block.get("input")
                    if isinstance(inputs, dict):
                        return _agents_server_tool_names.start_mode(str(block.get("name", "")).rsplit("__", 1)[-1], inputs)
        call = record.entry.get("payload")
        if isinstance(call, dict) and call.get("call_id") in ids and call.get("type") in {"function_call", "custom_tool_call"}:
            arguments = call.get("arguments")
            inputs = arguments if isinstance(arguments, dict) else _json_object(arguments)
            if inputs is not None:
                return _agents_server_tool_names.start_mode(str(call.get("name", "")).rsplit("__", 1)[-1], inputs)
    return None


def _collection_events(collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    """全収集照会が同じ記録由来と未解決の対象を出力する。"""
    return [
        *(
            {
                "kind": "record-provenance",
                "record": item.record_id,
                "role": item.role,
                "source_record": item.source_record,
                "source_line": item.source_line,
                "mode": _launch_mode(item, collected),
                "role_document": _role_document(item),
            }
            for item in collected
        ),
        *_unresolved_events(unresolved),
    ]


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


_RECORD_LOCATOR_NEXT_ACTION = (
    "`--detail`・`--record-schema`・`--context-at`へ`<記録ID>:<行番号>`の形で、"
    "オプションを指定しないときの出力が示す記録IDと行番号を渡して再実行する"
)


_CATALOG_ROOT_NEXT_ACTION = (
    "Claude Codeは`--catalog-claude-project`へ`~/.claude/projects/<プロジェクト>`の絶対パスを、"
    "Codexは`--catalog-codex-history`へCodexの記録ディレクトリの絶対パスを渡して再実行する"
)


_SINCE_NEXT_ACTION = (
    "`--since`へISO 8601形式の時刻（例: `2026-09-30T00:00:00+09:00`。"
    "タイムゾーンを省くと実行ホストのローカル時刻として扱う）を渡して再実行する"
)


_BOUNDARY_NEXT_ACTION = (
    "`--observation-boundary`へ`--since`以後のISO 8601形式の時刻（例: `2026-09-30T12:00:00+09:00`。"
    "タイムゾーンを省くと実行ホストのローカル時刻として扱う）を渡して再実行する"
)


_ELAPSED_UNTIL_NEXT_ACTION = (
    "`--elapsed-until`へ記録の最初のレコード以後の時刻をISO 8601形式（例: `2026-09-30T12:00:00+09:00`。"
    "タイムゾーンを省くと実行ホストのローカル時刻として扱う）で渡すか、時刻を持つ記録を対象にして再実行する"
)


def _error_event(text: str, *, next_action: str) -> dict[str, Any]:
    """照会不能を示すエラーイベントを、次の操作の項目`next_action`付きで返す。"""
    if not next_action.strip():
        raise ValueError("エラーイベントのnext_actionは空文字列以外で指定する必要がある")
    return {"kind": "error", "text": text, "next_action": next_action}


def _events_with_record(events: list[dict[str, Any]], record_id: str) -> list[dict[str, Any]]:
    """イベントを複製し、由来記録IDを付ける。"""
    return [{**event, "record": record_id} for event in events]


def _default_events(collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        events.extend(_events_with_record(_extract_records(item.records), item.record_id))
    events.extend(_collection_events(collected, unresolved))
    return events
