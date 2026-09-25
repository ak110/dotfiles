"""Claude CodeとCodexのtranscriptから振り返り用の時系列証拠を抽出し、照会する。

既定モードはセッション全体の時系列イベントをJSONLで出力し、各イベントへ由来行の行番号`line`を付ける。
`--warn`・`--grep`・`--detail`・`--stats`・`--hook-notices`・`--user-events`の照会モードは、抽出結果に無い詳細をtranscriptから
1コマンドで取得するためのもので、都度のワンライナーによる再解析を置き換える。
`--bundle`の集約実行は、通常表示と`--warn`・`--stats`・`--hook-notices`の走査を1回の記録読み込みでまとめて行い、
走査ごとの全量を指定ディレクトリ配下のファイルへ書いて標準出力へは要約だけを返す。

本スクリプトは検査スクリプトではなくデータ抽出ツールであるため、
`agent-toolkit:writing-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と照会不能（対象記録の読込不能・モード併用・不正な正規表現・範囲外の行番号）を
終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Literal, NamedTuple

try:
    from agent_toolkit._atk import config as _atk_config
except ImportError as _import_error:
    _SELF = Path(__file__).resolve()
    print(
        f"agent_toolkitパッケージを解決できません: {_import_error}。"
        "`atk run-script session-review-evidence -- <引数>`で起動してください。",
        file=sys.stderr,
    )
    sys.exit(2)

_MAX_TEXT_LENGTH = 2000
_MAX_DETAIL_LENGTH = 8000
_OMISSION_MARK = "…[省略]"
# Claude Codeのサブエージェントが報告本文を呼び出し元へ渡すツールの名前。
_HANDBACK_TOOL = "SubagentHandback"
_WARNING_LINE_PATTERN = re.compile(
    r"^(?:"
    r"\s*(?:\d+\t)?(?:"
    r"<(?:agent-toolkit-auto-inserted|agent-toolkit-hook-message)"
    r'(?=[^>]*\ssource="[^"]+")(?=[^>]*\skind="(?:warn|warning)")[^>]*>|'
    r"(?:\[auto-generated:[^\]]+\]\s*)?\[(?:warn|warning)\](?:\s|$)|"
    r"⚠(?:\s+|\s*[:：])"
    r")|"
    r"(?:warning|warn|警告)\s*[:：]"
    r")",
    re.IGNORECASE,
)
_ZERO_COUNT_ANNOTATION = r"(?:[\s]*[(（](?:warnings?|エラー|警告)?[\s:：]*0(?:件)?[)）])?"
"""不在を表す語の後に続く、件数が0であることを示す注記。

`警告: なし(warning: 0)`のように、検査の正常終了が件数の注記を伴う形で書かれる。
注記を不在判定の対象外にすると、正常終了の本文が警告候補として上がる。
一致の条件を件数が0の場合へ限り、0でない件数が続く本文を除外しない。
"""
_WARNING_ABSENCE_PATTERN = re.compile(
    r"(?:なし|無し|ありません|検出なし|0件|none|no|n/a|-)" + _ZERO_COUNT_ANNOTATION + r"[\s。.]*\Z",
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


def _is_hook_record(value: dict[str, Any]) -> bool:
    """hook実行の記録に当たるかを`type`の値で判定する。

    `type`の値は文字列とは限らない。ツール定義を含む記録では
    `attachment.tools[].schema.input_schema.properties`配下に`type`という名前の
    プロパティ定義が現れ、その値はJSON Schemaのdictになる。
    集合照合の前に文字列であることを確かめないと`TypeError`で走査が止まる。
    """
    return isinstance(value.get("type"), str) and value["type"] in _HOOK_RECORD_TYPES


_HOOK_NOTICE_MARKER = re.compile(
    r"(?:<(?:agent-toolkit-auto-inserted|agent-toolkit-hook-message)"
    r'(?=[^>]*\ssource="(?P<hook_xml>[^"]+)")(?=[^>]*\skind="(?P<tag_xml>[^"]+)")[^>]*>|'
    r"\[auto-generated:\s*(?P<hook_legacy>[^\]]*?)\s*\](?:\s*\[(?P<tag_legacy>[^\]]*)\])?)"
)
_HOOK_XML_END_TAGS = ("</agent-toolkit-auto-inserted>", "</agent-toolkit-hook-message>")
_HOOK_XML_END_MARKER = re.compile(r"</(?:agent-toolkit-auto-inserted|agent-toolkit-hook-message)>")
_CANDIDATE_KIND_LENGTH = 80
_PERMISSION_DENIAL_MARKER = "denied by the Claude Code auto mode classifier"
"""auto mode classifierの拒否本文に現れる定型句。実行環境が返す本文をそのまま用いる。"""
# 本文の可変部（語頭から始まるパスと、UWI識別子・行番号・トークン数などの数値）。種別キーの分裂を防ぐため置換する。
# パスは語頭に限定するが、数値列は語頭・語中を問わず置換するため、`github.com/ak110/dotfiles`のような
# 固定の識別子も数値部分が置換される。
_CANDIDATE_VARIABLE = re.compile(r"""(?<![^\s(\[<'"`])~?/[^\s`'"]+|\d+""")
_CANDIDATE_VARIABLE_PLACEHOLDER = "<var>"
_HOOK_FAILURE_PREFIX = re.compile(r"^[^\r\n]*?\bhook error:\s*\[[^\r\n]*?\]:\s*", re.IGNORECASE)
_EXIT_CODE_PREFIX = re.compile(r"^Exit code\s+\d+\s*(?:\r?\n)+", re.IGNORECASE)
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
    """解決できない委譲又は委譲先記録を機械可読イベントへ渡す。"""

    record_id: str
    line: int
    kind: Literal["unresolved-record", "unresolved-delegation"] = "unresolved-record"


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


def _question_answers_event(pairs: list[tuple[str, list[str], str]]) -> dict[str, Any] | None:
    """質問と回答を共通書式の単一userイベントへ変換する。"""
    sections: list[str] = []
    for question, answers, notes in pairs:
        clipped_answers = [_clip(answer) for answer in answers]
        answer_text = "\n".join(clipped_answers)
        section = f"質問: {_clip(question)}\n回答: {answer_text}"
        if notes:
            section += f"\n自由記述: {_clip(notes)}"
        sections.append(section)
    return _event("user", "\n".join(sections))


class _PendingQuestion(NamedTuple):
    """回答イベントの生成に要する、質問側の行番号と質問文ごとの選択肢label。"""

    line: int
    labels: dict[str, list[str]]


def _claude_question_options(content: Any) -> dict[str, dict[str, list[str]]]:
    """AskUserQuestionのtool_use IDごとに、質問文と選択肢labelの対応を取得する。

    labelは`input.questions[].options[].label`に現れる。回答の文字列を当該labelの集合と
    照合して、選択肢をそのまま選んだ回答と方針を是正した回答を判別する。
    """
    if not isinstance(content, list):
        return {}
    options: dict[str, dict[str, list[str]]] = {}
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") != "AskUserQuestion" or not isinstance(block.get("id"), str):
            continue
        options[block["id"]] = _claude_question_labels(block.get("input"))
    return options


def _claude_question_labels(payload: Any) -> dict[str, list[str]]:
    """AskUserQuestionの入力から、質問文ごとの選択肢labelを取得する。"""
    if not isinstance(payload, dict):
        return {}
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return {}
    labels: dict[str, list[str]] = {}
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("question"), str):
            continue
        choices = question.get("options")
        if not isinstance(choices, list):
            continue
        labels[question["question"]] = [
            choice["label"] for choice in choices if isinstance(choice, dict) and isinstance(choice.get("label"), str)
        ]
    return labels


def _is_offered_answer(answer: str, labels: list[str]) -> bool:
    """回答の文字列が、提示した選択肢のlabelだけで構成されるかを返す。

    複数選択の回答はlabelをカンマと空白で連結した1つの文字列として記録されるため、
    区切った全ての要素がlabelに一致する場合だけ、選択肢をそのまま選んだ回答とする。
    labelを取得できない記録では当該判別が成立しないため、選択肢をそのまま選んだ回答として扱う。
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
    選択肢のlabelと一致しない回答と、`notes`を持つ回答は従来の判断を是正した介入であるため、
    `answer_intervention`を付けて問題候補の母集団へ残す。
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
    labels: dict[str, list[str]] = {}
    for pending in matched:
        labels.update(pending.labels)
    if not isinstance(result, dict):
        return None
    answers = result.get("answers")
    if not isinstance(answers, dict) or not all(
        isinstance(question, str) and isinstance(answer, str) for question, answer in answers.items()
    ):
        return None
    annotations = result.get("annotations")
    pairs: list[tuple[str, list[str], str]] = []
    intervention = False
    for question, answer in answers.items():
        annotation = annotations.get(question) if isinstance(annotations, dict) else None
        raw_notes = annotation.get("notes") if isinstance(annotation, dict) else ""
        notes = raw_notes if isinstance(raw_notes, str) else ""
        if notes or not _is_offered_answer(answer, labels.get(question, [])):
            intervention = True
        pairs.append((question, [answer], notes))
    event = _question_answers_event(pairs)
    if event is not None:
        event["line"] = question_line
        if intervention:
            event["answer_intervention"] = True
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


def _is_runtime_generated(entry: dict[str, Any]) -> bool:
    """実行環境が生成したエントリであるかを構造上の標識で返す。

    実行環境は、画像の寸法や出力の切り詰めを伝える注記を利用者のロールを持つエントリへ書き込む。
    当該注記は本文の形からは利用者の発話と区別できないため、`isMeta`と`turnCompanion`の標識で判別する。
    """
    return entry.get("isMeta") is True or entry.get("turnCompanion") is True


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
    for line, entry in zip(lines, entries, strict=True):
        message = entry.get("message")
        if isinstance(message, dict) and entry.get("type") == "assistant":
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        tool_uses[block["id"]] = (
                            str(block.get("name", "")),
                            json.dumps(block.get("input"), ensure_ascii=False, sort_keys=True),
                        )
        for event in _claude_entry_events(entry, line, pending_claude_questions, subagent_record):
            if event.get("kind") == "failed-tool":
                tool_name, operation = tool_uses.get(str(event.get("tool", "")), ("", ""))
                event["tool_name"] = tool_name
                event["operation"] = operation
            event.setdefault("line", line)
            _set_entry_timestamp(event, entry)
            events.append(event)
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
                    call_id: _PendingQuestion(line, labels)
                    for call_id, labels in _claude_question_options(message.get("content")).items()
                }
            )
            for text in _text_blocks(message.get("content")):
                event = _event("assistant", text)
                if event:
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
    pairs: list[tuple[str, list[str], str]] = []
    for question_id, question in questions.items():
        answer_data = raw_answers.get(question_id)
        if not isinstance(answer_data, dict):
            continue
        answers = answer_data.get("answers")
        if not isinstance(answers, list) or not all(isinstance(answer, str) for answer in answers):
            continue
        pairs.append((question, answers, ""))
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
            _set_entry_timestamp(event, entry)
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
            event["executable"] = _basename(command[0]) if command else ""
        event["diagnostic"] = "" if text == "CommandExecution failed" else _clip(text)
        exit_code = item.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            event["exit_code"] = exit_code
    return event


def _handback_messages(content: Any) -> list[str]:
    """Claude Codeのサブエージェントが`SubagentHandback`で呼び出し元へ渡した報告本文を返す。"""
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
    """最終結果への置換と連番付けを行う。

    Claude Codeのサブエージェントは報告本文を`SubagentHandback`の引数で渡し、その後に定型文だけを書く。
    同呼び出しを持つ記録では、最後の同呼び出しの本文を最終結果とする。
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
# 新しい委譲記録の発見元は子sessionを生成する起動ツールに限る。
# 既存session操作と外側実行セルの入力文字列は、新しい委譲の証拠にならない。
_AGENTS_SERVER_TOOL_NAMES = frozenset(
    {
        *(f"mcp__plugin_agent-toolkit_agents_server__{name}" for name in ("start", "start_explore", "start_shell")),
        *(f"mcp__agents_server__{name}" for name in ("start", "start_explore", "start_shell")),
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


def _started_after_boundary(records: list[_Record], boundary: datetime.datetime) -> bool:
    """記録の最初の時刻が観測境界より後かを返す。"""
    timestamps = [_record_timestamp(record) for record in records]
    known = [timestamp for timestamp in timestamps if timestamp is not None]
    return bool(known) and min(known) > boundary


def _elapsed_until_event(records: list[_Record], until_text: str) -> dict[str, Any] | str:
    """最初の記録から指定時刻までの経過時間イベント又はエラー文を返す。"""
    try:
        until = _parse_timestamp(until_text)
    except ValueError:
        return f"経過時間の終端が不正: {until_text}"
    timestamps = [
        (timestamp, record.entry["timestamp"]) for record in records if (timestamp := _record_timestamp(record)) is not None
    ]
    if not timestamps:
        return ""
    start, start_text = min(timestamps, key=lambda item: item[0])
    if until < start:
        return f"経過時間の終端が最初のレコードより前: {until_text}"
    return {
        "kind": "session-elapsed",
        "start": start_text,
        "until": until_text,
        "elapsed_seconds": int((until - start).total_seconds()),
    }


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
    structured = value.get("structuredContent")
    if isinstance(structured, dict):
        for key in _THREAD_ID_KEYS:
            thread = structured.get(key)
            if isinstance(thread, str) and thread:
                return thread
    return None


def _agents_server_call_ids(records: list[_Record]) -> set[str]:
    """ClaudeとCodexのagents_server起動ツール呼び出しIDを返す。"""
    call_ids: set[str] = set()
    for record in records:
        message = record.entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                call_id = block.get("id")
                if isinstance(call_id, str) and block.get("name") in _AGENTS_SERVER_TOOL_NAMES:
                    call_ids.add(call_id)

        payload = record.entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") not in {"custom_tool_call", "function_call"}:
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            continue
        name = payload.get("name")
        if name in _AGENTS_SERVER_TOOL_NAMES:
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
    result_call_ids = (
        {
            block.get("tool_use_id")
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
        }
        if isinstance(content, list)
        else set()
    )

    if result_call_ids & agents_server_call_ids:
        mcp_meta = entry.get("mcpMeta")
        if isinstance(mcp_meta, dict):
            add_mapping(mcp_meta.get("structuredContent"))

        add_mapping(entry.get("toolUseResult"))

    payload = entry.get("payload")
    if isinstance(payload, dict):
        if payload.get("type") == "custom_tool_call_output" and payload.get("call_id") in agents_server_call_ids:
            output = payload.get("output")
            add_mapping(output)
            for text in _codex_text_blocks(output):
                add_mapping(text)
        if payload.get("type") == "function_call_output" and payload.get("call_id") in agents_server_call_ids:
            output = payload.get("output")
            add_mapping(output)
            for text in _codex_text_blocks(output):
                add_mapping(text)
        if entry.get("type") == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item")
            if (
                isinstance(item, dict)
                and item.get("server") == "agents_server"
                and item.get("tool") in _AGENTS_SERVER_TOOL_NAMES
            ):
                add_mapping(item.get("result"))

    notification_texts: list[str] = []
    if entry.get("type") == "queue-operation":
        notification_texts.extend(_text_blocks(entry.get("content")))
    notification_texts.extend(_text_blocks(content))
    for text in notification_texts:
        for match in _TASK_RESULT_PATTERN.finditer(text):
            add_mapping(match.group(1))
    return list(dict.fromkeys(found))


def _delegation_output_call_id(record: _Record, agents_server_call_ids: set[str]) -> str | None:
    """agents_server起動に対応するCodexの出力レコードから呼び出しIDを返す。"""
    payload = record.entry.get("payload")
    if not isinstance(payload, dict) or payload.get("type") not in {"custom_tool_call_output", "function_call_output"}:
        return None
    call_id = payload.get("call_id")
    return call_id if isinstance(call_id, str) and call_id in agents_server_call_ids else None


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
    boundary: datetime.datetime | None = None,
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
        unresolved_delegation_calls: set[str] = set()
        for subagent in _subagent_records(source):
            if boundary is not None and _started_after_boundary(subagent.records, boundary):
                continue
            resolved = subagent.path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            collected.append(subagent)
        for record in source.records:
            if boundary is not None and (timestamp := _record_timestamp(record)) is not None and timestamp > boundary:
                continue
            thread_ids = _thread_ids_from_record(record, agents_server_call_ids)
            call_id = _delegation_output_call_id(record, agents_server_call_ids)
            if call_id and not thread_ids and call_id not in unresolved_delegation_calls:
                unresolved.append(_UnresolvedRecord(source.record_id, record.line, "unresolved-delegation"))
                unresolved_delegation_calls.add(call_id)
            payload = record.entry.get("payload")
            item = payload.get("item") if isinstance(payload, dict) else None
            if (
                record.entry.get("type") == "event_msg"
                and isinstance(payload, dict)
                and payload.get("type") == "item_completed"
                and isinstance(item, dict)
                and item.get("server") == "agents_server"
                and item.get("tool") in _AGENTS_SERVER_TOOL_NAMES
                and not thread_ids
            ):
                unresolved.append(_UnresolvedRecord(source.record_id, record.line, "unresolved-delegation"))
            for engine, session_id in thread_ids:
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
    return [{"kind": item.kind, "record": item.record_id, "line": item.line} for item in unresolved]


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
    レコードとして1回の発生を記録する。Codexの所要時間は呼び出し側が計測記録から付与する。
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


def _codex_record_thread_id(item: _CollectedRecord) -> str | None:
    """Codex記録が属するthread IDを収集時の識別子又はsession metadataから返す。"""
    if item.runtime != "codex":
        return None
    if item.record_id.startswith("codex:"):
        return item.record_id.split(":", 1)[1]
    for record in item.records:
        entry = record.entry
        payload = entry.get("payload")
        if entry.get("type") == "session_meta" and isinstance(payload, dict):
            thread_id = payload.get("id")
            if isinstance(thread_id, str) and thread_id:
                return thread_id
    return None


def _compaction_durations(directory: Path, thread_id: str) -> list[float]:
    """threadの有効な計測記録から所要秒数を記録順で返す。"""
    try:
        lines = (directory / f"{thread_id}.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    durations: list[float] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("version") != 1 or record.get("thread_id") != thread_id:
            continue
        duration = record.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            durations.append(float(duration))
    return durations


def _stats_compaction_events(
    collected: list[_CollectedRecord],
    compaction_record_dir: Path,
) -> list[dict[str, Any]]:
    """全記録のコンパクションの発生位置と件数を返す。

    メイン記録・サブエージェント記録・委譲先セッションのいずれで発生した分も数える。
    発生が無い場合も件数0の集計イベントだけは返し、発生の有無を呼び出し側が判別できるようにする。
    """
    events: list[dict[str, Any]] = []
    for item in collected:
        thread_id = _codex_record_thread_id(item)
        durations = iter(_compaction_durations(compaction_record_dir, thread_id)) if thread_id is not None else iter(())
        for record in item.records:
            event = _compaction_event(record, item.record_id)
            if event is None:
                continue
            if event["engine"] == "codex":
                duration = next(durations, None)
                if duration is not None:
                    event["duration_seconds"] = duration
            events.append(event)
    events.sort(key=lambda event: (event["record"], event["line"]))
    counts = collections.Counter(event["record"] for event in events)
    total = {
        "kind": "stats-compaction-total",
        "count": len(events),
        "by_record": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "total_duration_seconds": round(
            sum((event["duration_seconds"] for event in events if "duration_seconds" in event), 0.0), 1
        ),
        "duration_unknown_count": sum("duration_seconds" not in event for event in events),
    }
    return [*events, total]


def _stats_events(collected: list[_CollectedRecord], compaction_record_dir: Path) -> list[dict[str, Any]]:
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
    events.extend(_stats_compaction_events(collected, compaction_record_dir))

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
    measured_threads = [event for event in events if event.get("kind") == "stats-agent-thread"]
    unmeasured_threads = [event["session_id"] for event in measured_threads if "start" not in event or "end" not in event]
    intervals: list[tuple[datetime.datetime, datetime.datetime, str]] = []
    for event in measured_threads:
        if event["session_id"] in unmeasured_threads:
            continue
        start = _parse_timestamp(event["start"])
        end = _parse_timestamp(event["end"])
        if start is not None and end is not None and start < end:
            intervals.append((start, end, event["session_id"]))
    main_start = _parse_timestamp(summary["start"]) if isinstance(summary.get("start"), str) else None
    main_end = _parse_timestamp(summary["end"]) if isinstance(summary.get("end"), str) else None
    if main_start is not None and main_end is not None and main_start < main_end:
        intervals = [(max(start, main_start), min(end, main_end), thread) for start, end, thread in intervals]
        intervals = [(start, end, thread) for start, end, thread in intervals if start < end]
        boundaries = sorted({main_start, main_end, *(point for start, end, _ in intervals for point in (start, end))})
        main_only = 0.0
        overlap = 0.0
        exclusive: collections.defaultdict[str, float] = collections.defaultdict(float)
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            active = [thread for left, right, thread in intervals if left <= start and end <= right]
            seconds = (end - start).total_seconds()
            if not active:
                main_only += seconds
            elif len(active) == 1:
                exclusive[active[0]] += seconds
            else:
                overlap += seconds
        events.append(
            {
                "kind": "stats-critical-path",
                "elapsed_seconds": round((main_end - main_start).total_seconds(), 1),
                "main_only_seconds": round(main_only, 1),
                "overlap_seconds": round(overlap, 1),
                "segments": [
                    {"owner": owner, "exclusive_seconds": round(seconds, 1)}
                    for owner, seconds in sorted(exclusive.items(), key=lambda item: (-item[1], item[0]))
                ],
                "unmeasured_threads": sorted(unmeasured_threads),
            }
        )
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


def _has_structured_warning_body(value: dict[str, Any]) -> bool:
    """警告本文として扱える明示フィールドが辞書に存在するかを返す。"""
    normalized_keys = {key.casefold() for key in value if isinstance(key, str)}
    return bool(normalized_keys.intersection(_STRUCTURED_WARNING_BODY_KEYS))


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
            if not in_body and _is_hook_record(value):
                found.append(value)
                return
            for key, item in value.items():
                collect(item, in_body=in_body or key in _BODY_KEYS)
        elif isinstance(value, list):
            for item in value:
                collect(item, in_body=in_body)

    collect(entry)
    return found


def _is_execution_tool_name(name: str | None) -> bool:
    """非構造化の実行時警告を返し得るツール名かを判定する。"""
    if not isinstance(name, str):
        return False
    leaf = name.casefold().rsplit("__", maxsplit=1)[-1].rsplit(".", maxsplit=1)[-1]
    return leaf in {"bash", "commandexecution", "exec_command", "start_batch", "start_shell"}


def _warning_tool_names(records: list[_Record]) -> dict[str, str]:
    """ツール結果の識別子を、先行する呼び出しのツール名へ対応付ける。"""
    names: dict[str, str] = {}
    for record in records:
        entry = record.entry
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id")
                name = block.get("name")
                if isinstance(tool_id, str) and isinstance(name, str):
                    names[tool_id] = name
        payload = entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") not in {"function_call", "custom_tool_call"}:
            continue
        call_id = payload.get("call_id")
        name = payload.get("name")
        if isinstance(call_id, str) and isinstance(name, str):
            names[call_id] = name
    return names


def _warning_result_values(entry: dict[str, Any], tool_names: dict[str, str]) -> list[tuple[Any, bool, bool]]:
    """警告を抽出できる結果値、hook由来及び非構造化本文の走査可否を返す。"""
    values: list[tuple[Any, bool, bool]] = []
    tool_use_result = entry.get("toolUseResult")
    is_read_result = isinstance(tool_use_result, dict) and "file" in tool_use_result

    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    result_blocks = (
        [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]
        if isinstance(content, list)
        else []
    )
    result_ids = [block.get("tool_use_id") for block in result_blocks if isinstance(block.get("tool_use_id"), str)]
    known_result_names = [tool_names[tool_id] for tool_id in result_ids if tool_id in tool_names]
    stream_result = isinstance(tool_use_result, dict) and any(key in tool_use_result for key in ("stdout", "stderr"))
    allow_tool_use_result_markers = (
        any(_is_execution_tool_name(name) for name in known_result_names) if known_result_names else stream_result
    )

    if "toolUseResult" in entry and not is_read_result:
        values.append((tool_use_result, False, allow_tool_use_result_markers))

    if isinstance(content, list) and not is_read_result:
        for block in result_blocks:
            tool_id = block.get("tool_use_id")
            tool_name = tool_names.get(tool_id) if isinstance(tool_id, str) else None
            values.append((block, False, tool_name is not None and _is_execution_tool_name(tool_name)))

    payload = entry.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "function_call_output":
        call_id = payload.get("call_id")
        tool_name = tool_names.get(call_id) if isinstance(call_id, str) else None
        values.append((payload.get("output"), False, tool_name is not None and _is_execution_tool_name(tool_name)))
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
        call_id = payload.get("call_id")
        tool_name = tool_names.get(call_id) if isinstance(call_id, str) else None
        values.append((payload.get("output"), False, tool_name is not None and _is_execution_tool_name(tool_name)))
    if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "item_completed":
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "CommandExecution":
            values.extend((item.get(key), False, True) for key in ("aggregated_output", "output", "stdout", "stderr"))

    values.extend((hook_record, True, True) for hook_record in _warning_hook_records(entry))
    return values


def _warning_texts(entry: dict[str, Any], tool_names: dict[str, str] | None = None) -> list[str]:
    """本文の由来に基づき、実行結果領域から実行時警告の本文行を返す。

    フック通知標識はhook実行の記録に由来する場合だけ採用する。コマンド出力に由来する通常の
    実行時警告は検出対象として維持し、問題の不在を述べる本文は除外する。
    """
    bodies: list[tuple[str, bool, bool]] = []

    def collect_markers(value: Any, from_hook_record: bool) -> None:
        if isinstance(value, str):
            bodies.append((value, True, from_hook_record))
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(parsed, (dict, list)):
                collect_markers(parsed, from_hook_record)
            return
        if isinstance(value, dict):
            for item in value.values():
                collect_markers(item, from_hook_record)
        elif isinstance(value, list):
            for item in value:
                collect_markers(item, from_hook_record)

    def collect_structured(value: Any, from_hook_record: bool) -> None:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(parsed, (dict, list)):
                collect_structured(parsed, from_hook_record)
            return
        if isinstance(value, dict):
            warning_values, direct_warning = _structured_warning_fields(value)
            for warning_value in warning_values:
                for text in _structured_warning_value_texts(warning_value):
                    bodies.append((text, False, from_hook_record))
            if direct_warning and not warning_values and _has_structured_warning_body(value):
                for text in _structured_warning_value_texts(value):
                    bodies.append((text, False, from_hook_record))
            for item in value.values():
                collect_structured(item, from_hook_record)
        elif isinstance(value, list):
            for item in value:
                collect_structured(item, from_hook_record)

    for result_value, from_hook_record, allow_markers in _warning_result_values(entry, tool_names or {}):
        if allow_markers:
            collect_markers(result_value, from_hook_record)
        collect_structured(result_value, from_hook_record)
    unnumbered_by_body = [
        {line.strip() for line in text.splitlines() if _LINE_NUMBER_PREFIX.match(line) is None} for text, _, _ in bodies
    ]
    seen: set[str] = set()
    result: list[str] = []
    for body_index, (text, marker_only, from_hook_record) in enumerate(bodies):
        normalized_text = " ".join(text.split())
        xml_marker = _HOOK_NOTICE_MARKER.match(normalized_text)
        if (
            marker_only
            and from_hook_record
            and xml_marker is not None
            and xml_marker.group("tag_xml") in _STRUCTURED_WARNING_VALUES
        ):
            warning_body = normalized_text[xml_marker.end() :].strip()
            for end_tag in _HOOK_XML_END_TAGS:
                if warning_body.endswith(end_tag):
                    warning_body = warning_body[: -len(end_tag)].rstrip()
                    break
            if warning_body and not _WARNING_ABSENCE_PATTERN.fullmatch(warning_body) and warning_body not in seen:
                seen.add(warning_body)
                result.append(warning_body)
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or not (not marker_only or _WARNING_LINE_PATTERN.search(line)):
                continue
            if _HOOK_NOTICE_MARKER.search(line) and not from_hook_record:
                continue
            warning_body = stripped
            if numbered_body := _LINE_NUMBER_PREFIX.match(warning_body):
                warning_body = numbered_body.group(1).strip()
            if warning_marker := _WARNING_LINE_PATTERN.search(warning_body):
                warning_body = warning_body[warning_marker.end() :].strip()
            if not warning_body or _WARNING_ABSENCE_PATTERN.fullmatch(warning_body):
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
    tool_names = _warning_tool_names(scannable)
    for record in scannable:
        matched_lines = _warning_texts(record.entry, tool_names)
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
        events.extend(
            {"kind": "match", "line": record.line, "timestamp": _entry_timestamp(record.entry), "text": _clip(line_text)}
            for line_text in matched_lines
        )
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
                for key in _hook_notice_keys(body, hook_name if isinstance(hook_name, str) else None):
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


def _hook_notice_candidate_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """通知の集計前の位置を、一次選別候補として重複なく返す。"""
    events: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, _HookNoticeKey]] = set()
    for item in collected:
        for record in item.records:
            for hook_record in _hook_records(record.entry):
                tool_use_id = hook_record.get("toolUseID")
                normalized_id = tool_use_id if isinstance(tool_use_id, str) else None
                hook_name = hook_record.get("hookName")
                for body in _hook_notice_bodies(hook_record):
                    for key in _hook_notice_keys(body, hook_name if isinstance(hook_name, str) else None):
                        identity = (item.record_id, normalized_id, key)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        events.append(
                            {
                                "kind": "hook-notice",
                                "record": item.record_id,
                                "line": record.line,
                                "text": key.kind_text,
                                "hook": key.hook,
                                "hook_name": key.hook_name,
                                "tag": key.tag,
                            }
                        )
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
        if _is_hook_record(value):
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


def _hook_notice_keys(body: str, hook_name: str | None) -> list[_HookNoticeKey]:
    """外側の通知境界ごとに発動元、重要度及び本文を返す。"""
    if not body.strip():
        return []
    openings = list(_HOOK_NOTICE_MARKER.finditer(body))
    if not openings:
        return [_HookNoticeKey(None, hook_name, None, _normalize_candidate_kind_text(body))]
    boundaries = sorted(
        [*openings, *_HOOK_XML_END_MARKER.finditer(body)],
        key=lambda marker: marker.start(),
    )
    keys: list[_HookNoticeKey] = []
    outer: re.Match[str] | None = None
    depth = 0

    def append_notice(marker: re.Match[str], end: int) -> None:
        source = marker.group("hook_xml") or marker.group("hook_legacy")
        tag = marker.group("tag_xml") or marker.group("tag_legacy")
        text = body[marker.end() : end]
        keys.append(_HookNoticeKey(source or None, hook_name, tag or None, _normalize_candidate_kind_text(text)))

    for marker in boundaries:
        if marker.re is _HOOK_XML_END_MARKER:
            if depth:
                depth -= 1
                if not depth and outer is not None:
                    append_notice(outer, marker.start())
                    outer = None
            continue
        if depth:
            if marker.group("hook_xml") is not None:
                depth += 1
            continue
        if outer is not None:
            append_notice(outer, marker.start())
        outer = marker
        depth = 1 if marker.group("hook_xml") is not None else 0
    if outer is not None:
        append_notice(outer, len(body))
    return keys


def _normalize_candidate_kind_text(text: str) -> str:
    """本文を、可変部を置換した先頭一定長の種別テキストへ正規化する。

    可変部を残すと同じ原因の事象が複数の候補へ分かれ、長さが不足すると別原因の事象が
    同一候補へ統合されるため、長さは実測に基づいて確定する。
    """
    without_hook_prefix = _HOOK_FAILURE_PREFIX.sub("", text, count=1)
    without_common_prefix = _EXIT_CODE_PREFIX.sub("", without_hook_prefix, count=1)
    normalized = _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(without_common_prefix.split()))
    return normalized[:_CANDIDATE_KIND_LENGTH]


def _normalize_hook_candidate_text(text: str) -> str:
    """hook通知の定型標識を除いた是正本文を候補種別へ正規化する。"""
    match = _HOOK_NOTICE_MARKER.search(text)
    if match is None:
        return text[:_CANDIDATE_KIND_LENGTH]
    return _normalize_candidate_kind_text(text[match.end() :].strip())


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


def _entry_detail_events(line: int, entry: dict[str, Any], *, limit: int = _MAX_DETAIL_LENGTH) -> list[dict[str, Any]]:
    """1エントリの詳細を、tool_use・tool_resultのブロック単位で整形する。

    クリップの上限はエントリ全体で共有し、ブロックの出現順に予算を配分する。
    予算超過で省略が生じたエントリは、当該エントリの全イベントへ`omitted`を付ける。
    予算が尽きた後の本文は空文字列となるため、この標識が無ければ
    空の出力が元から空だったのか省略の結果なのかを判別できない。
    各イベントは元エントリの`timestamp`を持ち、区間境界の時刻を元記録を読み直さずに確定できるようにする。
    """
    budget = _DetailBudget(limit)
    timestamp = _entry_timestamp(entry)
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
                        "timestamp": timestamp,
                        "name": str(block.get("name", "")),
                        "input": _clip_structure(block.get("input"), budget),
                    }
                )
            elif block.get("type") == "tool_result":
                events.append(
                    {
                        "kind": "detail",
                        "line": line,
                        "timestamp": timestamp,
                        "tool": str(block.get("tool_use_id", "")),
                        "text": budget.clip(_tool_result_body(block, entry)),
                    }
                )
    if not events:
        events = [
            {
                "kind": "detail",
                "line": line,
                "timestamp": timestamp,
                "text": budget.clip(json.dumps(entry, ensure_ascii=False, indent=2)),
            }
        ]
    if budget.omitted:
        for event in events:
            event["omitted"] = True
    return events


def _entry_timestamp(entry: dict[str, Any]) -> str | None:
    """元記録行の時刻を返す。時刻を持たないエントリは`None`とする。"""
    timestamp = entry.get("timestamp")
    return timestamp if isinstance(timestamp, str) else None


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
        text = " ".join(part for part in command if isinstance(part, str)).strip()
        return _clip(text.splitlines()[0]) if text else None
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


def _user_events_since(collected: list[_CollectedRecord], since: datetime.datetime) -> list[dict[str, Any]]:
    """メイン記録の状態を保ち、指定時刻より後に成立した利用者イベントだけを返す。"""
    events: list[dict[str, Any]] = []
    for item in collected:
        if item.record_id != "main":
            continue
        runtime = _detect_runtime([record.entry for record in item.records])
        if runtime is None:
            break
        selected_events: list[dict[str, Any]] = []
        pending_claude_questions: dict[str, _PendingQuestion] = {}
        pending_questions: dict[str, tuple[int, dict[str, str]]] = {}
        subagent_record = _is_subagent_record([record.entry for record in item.records])
        for record in item.records:
            record_events = (
                _codex_entry_events(record.entry, record.line, pending_questions)
                if runtime == "codex"
                else _claude_entry_events(record.entry, record.line, pending_claude_questions, subagent_record)
            )
            for event in record_events:
                event.setdefault("line", record.line)
            timestamp = _record_timestamp(record)
            if timestamp is not None and timestamp > since:
                selected_events.extend(record_events)
        user_events = [event for event in _finalize(selected_events) if event["kind"] == "user"]
        events.extend(_events_with_record(user_events, item.record_id))
        break
    events.append({"kind": "summary", "count": len(events)})
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


_BUNDLE_SCAN_FILENAMES = (
    "timeline.jsonl",
    "warnings.jsonl",
    "stats.jsonl",
    "hook-notices.jsonl",
    "candidates.jsonl",
    "candidate-evidence.jsonl",
)
_BUNDLE_BODY_KINDS = frozenset({"failed-tool", "agent-completion", "final-result"})
_BUNDLE_LOCATOR_ONLY_KINDS = frozenset({"user"})
_BUNDLE_BODY_LENGTH = 200
_BUNDLE_WARNING_GROUP_LENGTH = 120
_BUNDLE_WARNING_SAMPLE_COUNT = 3
_HOOK_NOTICE_VARIANT_LIMIT = 5
_RETURN_STATUS_PREFIX = "status:"
_ESCALATION_RETURN_STATUS = "needs_escalation"
_CANDIDATE_EVIDENCE_LENGTH = 2000
UNTRUNCATED_EVIDENCE_KINDS = frozenset(
    {"user-intervention", "command-failure", "tool-failure", "delegate-return", "escalation"}
)
"""個別証拠の本文を切り詰めない候補種別。

これらの本文は後続の分析主体が元記録を開かずに判断するための一次資料であり、切り詰めると
裏取りのために元記録を読み直す工程が生じる。hook通知など定型本文の種別だけに上限を残す。
"""
_TOOL_USE_EVIDENCE_KINDS = frozenset({"hook-notice", "command-failure", "tool-failure", "permission-denial"})
"""個別証拠へ対象のツール呼び出しの入力を加える候補種別。"""
_HOOK_NOTICE_CANDIDATE_TAGS = frozenset({"block", "warn"})
_CANDIDATE_USER_CONTEXT_LIMIT_PER_SIDE = 1


def _bundle_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    directory: Path,
    compaction_record_dir: Path,
) -> tuple[list[dict[str, Any]], int]:
    """4走査を1回の記録読み込みで行い、走査ごとの全量をファイルへ書いて要約だけを返す。

    標準出力へ返す要約の項目は、抽出担当が走査ごとの全量をファイルへ保存し、自作の集計コマンドで
    再加工していた工程を代替する目的で設けた。項目を減らすと当該工程が抽出担当側へ戻るため、
    取捨は代替対象の集計を確認してから判断する。
    保存先のファイルと同じ内容になる集計と通知の走査は標準出力へ返さない。呼び出し元が同じ内容を
    ファイルと標準出力の双方から受け取ると、標準出力の分量が実行環境の切り詰めに達するためである。
    未解決記録のイベントはどのファイルにも保存しないため、標準出力へ1回だけ書く。各ファイルの内容は、
    当該走査を単独で実行した出力から未解決記録のイベントを除いたものと一致する。
    """
    if not directory.is_dir():
        return [{"kind": "error", "text": f"出力先が実在するディレクトリでない: {directory}"}], 2
    resolved = directory.resolve()
    timeline = _default_events(collected, [])
    warnings = _warning_collection_events(collected, [])
    stats = _stats_events(collected, compaction_record_dir)
    hook_notices = _hook_notice_events([record for item in collected for record in item.records])
    candidates = _candidate_events(timeline, warnings, _hook_notice_candidate_events(collected))

    candidate_evidence = _write_candidate_evidence_files(
        resolved, _candidate_evidence_events(collected, candidates, timeline, warnings, hook_notices)
    )
    events: list[dict[str, Any]] = []
    scans = (timeline, warnings, stats, hook_notices, candidates, candidate_evidence)
    for filename, scan_events in zip(_BUNDLE_SCAN_FILENAMES, scans, strict=True):
        path = resolved / filename
        path.write_text(
            "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in scan_events),
            encoding="utf-8",
        )
        events.append({"kind": "bundle-file", "path": str(path), "count": len(scan_events)})
    candidate_items = [item for item in candidates if item.get("kind") == "candidate"]
    events.append(
        {
            "kind": "bundle-evidence-metrics",
            "full_scan_lines": len(timeline) + len(warnings) + len(hook_notices),
            "full_scan_bytes": sum(
                (resolved / filename).stat().st_size for filename in ("timeline.jsonl", "warnings.jsonl", "hook-notices.jsonl")
            ),
            "candidate_evidence_lines": len(candidate_evidence),
            "candidate_evidence_bytes": (resolved / "candidate-evidence.jsonl").stat().st_size,
            "decision_count": len(candidate_items),
            "analysis_group_count": len(
                {json.dumps(item["analysis_group_hint"], ensure_ascii=False) for item in candidate_items}
            ),
        }
    )
    events.extend(_bundle_timeline_events(timeline))
    events.extend(_bundle_warning_events(warnings))
    events.extend(_unresolved_events(unresolved))
    return events, 0


def _candidate_events(
    timeline: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    hook_notices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """決定的に除外できる入力を省き、同種の候補を全位置付きで集約する。

    母集団はhook通知、利用者介入、失敗したツール実行、警告及び工程の返却値とする。
    返却値を含めるのは、終了状態にかかわらず本文に誤りがある委譲結果も他の事象には現れず、
    本文の判定前に候補集合から漏れるためである。

    同じ位置の同一hook発火は構造化されたhook通知を代表とする。それ以外は、同じ位置でも候補種別又はhookタグが異なる事象を別候補として保持する。同じ位置、候補種別及びhookタグの
    組だけを重複として除外する。`permission-denial`は`failed-tool`の一部でもあるため、同じ位置の
    `tool-failure`も保持し、許可ルールと実行失敗の双方の見直しへ対応付ける。

    候補件数の削減は、正規化した本文での集約と、利用者介入ではない入力の除外だけで行う。
    `hook-notice`の既存の限定を除いて件数上限を設けない。振り返りの契約は、候補が保持する位置の集合と
    判定表の位置の集合の一致を求めるため、位置を失う削減は当該検査と両立しない。
    """
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    seen: set[tuple[str, int, str, str]] = set()
    excluded: collections.Counter[str] = collections.Counter()
    notices_by_locator: dict[tuple[str, int], list[dict[str, Any]]] = collections.defaultdict(list)
    for notice in hook_notices:
        if (
            notice.get("kind") == "hook-notice"
            and isinstance(notice.get("record"), str)
            and isinstance(notice.get("line"), int)
        ):
            notices_by_locator[(notice["record"], notice["line"])].append(notice)
    initial_skill_request, initial_skill_body = _initial_skill_input_locators(timeline)
    first_main_user: tuple[str, int] | None = None
    for event in timeline:
        line = event.get("line")
        text = event.get("text")
        if event.get("kind") == "user" and event.get("record") == "main" and isinstance(line, int) and isinstance(text, str):
            if _user_candidate_exclusion(event, "main", line, " ".join(text.split()), None) is None:
                first_main_user = ("main", line)
            break
    sources = (
        ("hook-notice", (event for event in hook_notices if event.get("kind") == "hook-notice")),
        ("user-intervention", (event for event in timeline if event.get("kind") == "user")),
        ("permission-denial", (event for event in timeline if _is_permission_denial(event))),
        (
            "command-failure",
            (event for event in timeline if event.get("kind") == "failed-tool" and event.get("tool") == "CommandExecution"),
        ),
        (
            "tool-failure",
            (event for event in timeline if event.get("kind") == "failed-tool" and event.get("tool") != "CommandExecution"),
        ),
        ("warning", (event for event in warnings if event.get("kind") == "warning")),
        ("escalation", (event for event in timeline if _is_escalation_return(event))),
        ("delegate-return", (event for event in timeline if _is_delegate_return(event))),
    )
    for candidate_kind, events in sources:
        for event in events:
            record = event.get("record")
            line = event.get("line")
            if not isinstance(record, str) or not isinstance(line, int):
                continue
            identity = (record, line, candidate_kind, str(event.get("tag", "")))
            if identity in seen:
                excluded["duplicate-candidate"] += 1
                continue
            seen.add(identity)
            text = event.get("text")
            normalized_text = " ".join(text.split()) if isinstance(text, str) else ""
            if candidate_kind in {"warning", "tool-failure"} and any(
                _same_hook_event(candidate_kind, event, notice) for notice in notices_by_locator.get((record, line), [])
            ):
                excluded["hook-notice-represented"] += 1
                continue
            if candidate_kind == "user-intervention":
                exclusion = _user_candidate_exclusion(
                    event,
                    record,
                    line,
                    normalized_text,
                    first_main_user,
                    initial_skill_request=initial_skill_request,
                    initial_skill_body=initial_skill_body,
                )
                if exclusion is not None:
                    excluded[exclusion] += 1
                    continue
            if candidate_kind == "command-failure" and _is_help_command_failure(event):
                excluded["command-help"] += 1
                continue
            if candidate_kind == "command-failure" and _is_normal_negative_result(event):
                excluded["normal-negative-result"] += 1
                continue
            if candidate_kind == "hook-notice":
                exclusion = _hook_notice_candidate_exclusion(event.get("tag"))
                if exclusion is not None:
                    excluded[exclusion] += 1
                    continue
            key = _candidate_key(candidate_kind, event, normalized_text)
            groups.setdefault(key, []).append(event)

    selected_groups: list[tuple[tuple[str, ...], list[dict[str, Any]], int, int]] = []
    bounded_hook_groups: dict[tuple[str, ...], list[tuple[tuple[str, ...], list[dict[str, Any]]]]] = {}
    for key, events in groups.items():
        if _is_bounded_hook_group(key):
            bounded_hook_groups.setdefault((key[1], key[3]), []).append((key, events))
        else:
            selected_groups.append((key, events, len(events), 0))
    for variants in bounded_hook_groups.values():
        ranked = sorted(variants, key=lambda item: (-len(item[1]), item[0]))
        for index, (key, events) in enumerate(ranked):
            if index >= _HOOK_NOTICE_VARIANT_LIMIT:
                excluded["hook-notice-detail-budget"] += len(events)
                continue
            representative = min(events, key=lambda event: (str(event["record"]), int(event["line"])))
            omitted_count = len(events) - 1
            excluded["hook-notice-detail-budget"] += omitted_count
            selected_groups.append((key, [representative], len(events), omitted_count))

    candidates: list[dict[str, Any]] = []
    included_locators: list[dict[str, Any]] = []
    included_locator_keys: set[tuple[str, int]] = set()
    for index, (key, events, occurrence_count, omitted_locator_count) in enumerate(
        sorted(selected_groups, key=lambda item: item[0]),
        start=1,
    ):
        locators: list[dict[str, Any]] = [{"record": str(event["record"]), "line": int(event["line"])} for event in events]
        locators.sort(key=lambda locator: (str(locator["record"]), int(locator["line"])))
        for locator in locators:
            locator_key = (str(locator["record"]), int(locator["line"]))
            if locator_key not in included_locator_keys:
                included_locator_keys.add(locator_key)
                included_locators.append(locator)
        candidate: dict[str, Any] = {
            "kind": "candidate",
            "candidate_id": f"c{index:04d}",
            "candidate_kind": key[0],
            "analysis_group_hint": list(
                key[1:] if key[0] in {"escalation", "hook-notice", "tool-failure"} else key[1:-1] if len(key) > 2 else key[1:]
            ),
            "event_key": list(key[1:]),
            "count": len(locators),
            "locators": locators,
        }
        text = events[0].get("text")
        if isinstance(text, str):
            candidate["text"] = text
        if _is_bounded_hook_group(key):
            candidate["occurrence_count"] = occurrence_count
            candidate["omitted_locator_count"] = omitted_locator_count
        candidates.append(candidate)
    included_locators.sort(key=lambda locator: (locator["record"], locator["line"]))
    return [
        *candidates,
        {
            "kind": "candidate-summary",
            "count": len(candidates),
            "included_locator_count": len(included_locators),
            "included_locators": included_locators,
            "excluded": dict(sorted(excluded.items())),
        },
    ]


_CANDIDATE_EVIDENCE_DIRNAME = "candidate-evidence"
"""候補ごとの証拠を保存するbundle直下のディレクトリ名。"""


def _write_candidate_evidence_files(directory: Path, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """候補ごとの証拠を個別ファイルへ保存し、索引の行を返す。

    完全分析は候補単位で行うため、証拠の保存単位も候補単位とする。
    1ファイルへ集約すると、消費側は全候補の長文証拠を読み込むか、
    出力上限に達した後で範囲を指定して取得し直すことになる。
    索引は候補ID、証拠件数、個別ファイルの相対パス及び総文字数を持ち、
    一次選別で除外した候補の本文を読まずに完全分析の対象を選べるようにする。
    """
    evidence_dir = directory / _CANDIDATE_EVIDENCE_DIRNAME
    evidence_dir.mkdir(exist_ok=True)
    index: list[dict[str, Any]] = []
    for item in evidence:
        candidate_id = str(item["candidate_id"])
        body = json.dumps(item, ensure_ascii=False)
        (evidence_dir / f"{candidate_id}.json").write_text(f"{body}\n", encoding="utf-8")
        index.append(
            {
                "kind": "candidate-evidence-index",
                "candidate_id": candidate_id,
                "analysis_group_hint": item["analysis_group_hint"],
                "locators": item["locators"],
                "evidence_count": len(item["events"]),
                "path": f"{_CANDIDATE_EVIDENCE_DIRNAME}/{candidate_id}.json",
                "total_chars": len(body),
            }
        )
    return index


def _candidate_evidence_events(
    collected: list[_CollectedRecord],
    candidates: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    hook_notices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """候補ごとに完全分析へ必要な位置付き証拠を有界な本文で束ねる。"""
    raw_entries = {(record.record_id, entry.line): entry.entry for record in collected for entry in record.records}
    indexed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for event in (*timeline, *warnings, *hook_notices):
        record, line = event.get("record"), event.get("line")
        if isinstance(record, str) and isinstance(line, int):
            indexed.setdefault((record, line), []).append(event)
    tool_uses = _tool_use_index(collected)
    evidence: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("kind") != "candidate":
            continue
        untruncated = candidate["candidate_kind"] in UNTRUNCATED_EVIDENCE_KINDS
        text_limit = sys.maxsize if untruncated else _CANDIDATE_EVIDENCE_LENGTH
        budget = _DetailBudget(text_limit)
        details: list[dict[str, Any]] = []
        source_chars = 0
        for locator in candidate["locators"]:
            key = (str(locator["record"]), int(locator["line"]))
            raw_entry = raw_entries.get(key)
            if raw_entry is not None:
                source_chars += len(json.dumps(raw_entry, ensure_ascii=False))
                details.extend(
                    _clip_structure({"record": key[0], **event}, budget)
                    for event in _entry_detail_events(key[1], raw_entry, limit=text_limit)
                )
                if candidate["candidate_kind"] in _TOOL_USE_EVIDENCE_KINDS:
                    for call_id in _evidence_call_ids(raw_entry):
                        tool_use = tool_uses.get((key[0], call_id))
                        if tool_use is not None:
                            details.append(_clip_structure({"record": key[0], **tool_use}, budget))
            else:
                for event in indexed.get(key, ()):  # 同一位置の別走査結果も保持する。
                    detail = {"kind": str(event.get("kind", "")), "record": key[0], "line": key[1]}
                    for field in ("tool", "hook", "hook_name", "tag"):
                        if field in event:
                            detail[field] = event[field]
                    if isinstance(event.get("text"), str):
                        source_chars += len(event["text"])
                        detail["text"] = budget.clip(event["text"])
                    details.append(detail)
            user_context = [
                event
                for event in timeline
                if event.get("kind") == "user" and event.get("record") == key[0] and isinstance(event.get("line"), int)
            ]
            before = sorted(
                (event for event in user_context if int(event["line"]) < key[1]), key=lambda event: -int(event["line"])
            )
            after = sorted(
                (event for event in user_context if int(event["line"]) > key[1]), key=lambda event: int(event["line"])
            )
            for direction, neighbors in (("before", before), ("after", after)):
                for event in neighbors[:_CANDIDATE_USER_CONTEXT_LIMIT_PER_SIDE]:
                    details.append(
                        {
                            "kind": "user-context",
                            "direction": direction,
                            "record": key[0],
                            "line": int(event["line"]),
                            "text": budget.clip(str(event.get("text", ""))),
                        }
                    )
        if not details:
            details.append(
                {
                    "kind": str(candidate["candidate_kind"]),
                    "record": str(candidate["locators"][0]["record"]),
                    "line": int(candidate["locators"][0]["line"]),
                    "text": budget.clip(str(candidate.get("text", ""))),
                }
            )
        item = {
            "kind": "candidate-evidence",
            "candidate_id": candidate["candidate_id"],
            "analysis_group_hint": candidate["analysis_group_hint"],
            "locators": candidate["locators"],
            "source_chars": source_chars,
            "text_limit": None if untruncated else _CANDIDATE_EVIDENCE_LENGTH,
            "user_context_limit_per_side": _CANDIDATE_USER_CONTEXT_LIMIT_PER_SIDE,
            "events": details,
        }
        if budget.omitted:
            item["omitted"] = True
        evidence.append(item)
    return evidence


def _tool_use_index(collected: list[_CollectedRecord]) -> dict[tuple[str, str], dict[str, Any]]:
    """記録ごとに、呼び出し識別子からツール呼び出しの入力と記録位置を引ける索引を作成する。

    hook通知と失敗したツール結果は呼び出し識別子だけを持ち、対象のコマンドは別の行にある。
    Claude Codeは`tool_use`ブロックの`id`、Codexは`function_call`等の`call_id`で対応付ける。
    """
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in collected:
        for record in item.records:
            message = record.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                        index[(item.record_id, block["id"])] = {
                            "kind": "tool-use",
                            "line": record.line,
                            "timestamp": _entry_timestamp(record.entry),
                            "name": str(block.get("name", "")),
                            "input": block.get("input"),
                        }
            payload = record.entry.get("payload")
            if (
                isinstance(payload, dict)
                and payload.get("type") in {"function_call", "custom_tool_call"}
                and isinstance(payload.get("call_id"), str)
            ):
                index[(item.record_id, payload["call_id"])] = {
                    "kind": "tool-use",
                    "line": record.line,
                    "timestamp": _entry_timestamp(record.entry),
                    "name": str(payload.get("name", "")),
                    "input": payload.get("arguments", payload.get("input")),
                }
    return index


def _evidence_call_ids(entry: dict[str, Any]) -> list[str]:
    """候補の記録行が参照する呼び出し識別子を出現順に重複なく返す。"""
    call_ids: list[str] = []
    for hook_record in _hook_records(entry):
        tool_use_id = hook_record.get("toolUseID")
        if isinstance(tool_use_id, str) and tool_use_id not in call_ids:
            call_ids.append(tool_use_id)
    for call_id in sorted(_result_call_ids(entry)):
        if call_id not in call_ids:
            call_ids.append(call_id)
    return call_ids


def _is_permission_denial(event: dict[str, Any]) -> bool:
    """Auto mode classifierの拒否で終わったツール実行であるかを返す。

    拒否は`is_error`のtool_resultとして記録されるため`failed-tool`の一部に当たる。
    許可ルールの見直しという他の失敗とは別の是正へ結び付くため、独立した候補種別として扱う。
    """
    if event.get("kind") != "failed-tool":
        return False
    text = event.get("text")
    return isinstance(text, str) and _PERMISSION_DENIAL_MARKER in text


def _is_delegate_return(event: dict[str, Any]) -> bool:
    """委譲先の空でない最終返却のうち、明示的なエスカレーション以外を返す。

    `final-result`は記録ごとの最後の非commentaryのアシスタントイベントであり、
    委譲先の記録では当該委譲先が呼び出し元へ返した返却値に対応する。
    成功の`status`があっても本文に誤りがあり得るため、終了状態で除外しない。
    メイン記録の最終出力は委譲返却ではない。明示的なエスカレーションは独立した候補へ送る。
    """
    if event.get("kind") != "final-result" or event.get("record") == "main":
        return False
    text = event.get("text")
    return isinstance(text, str) and bool(text.strip()) and not _is_escalation_return(event)


def _is_escalation_return(event: dict[str, Any]) -> bool:
    """上位判断を要求する明示的な最終返却であるかを返す。"""
    if event.get("kind") != "final-result":
        return False
    text = event.get("text")
    if not isinstance(text, str):
        return False
    return any(
        line.strip().removeprefix(_RETURN_STATUS_PREFIX).strip() == _ESCALATION_RETURN_STATUS
        for line in text.splitlines()
        if line.strip().startswith(_RETURN_STATUS_PREFIX)
    )


def _hook_notice_candidate_exclusion(tag: Any) -> str | None:
    """是正を求めない区分のhook通知を問題候補から除く場合に、除外の種別名を返す。

    この判定は、是正を求める通知が`block`又は`warn`の区分を必ず持つという前提へ依存する。
    残す区分を列挙するのは、規範の注入や配送の種別のように是正の要否と無関係な値を`kind`へ持つ出力が
    今後追加されても、既知値の列挙から漏れて是正要求として扱われないようにするためである。
    区分を持たない通知は、区分を示す必要が無い通知として発行されるため情報提示と同じ扱いとする。
    """
    if tag in _HOOK_NOTICE_CANDIDATE_TAGS:
        return None
    if tag in {"info", "notice"}:
        return "hook-notice-informational"
    if tag is None:
        return "hook-notice-untagged"
    return "hook-notice-context"


def _user_candidate_exclusion(
    event: dict[str, Any],
    record: str,
    line: int,
    text: str,
    first_main_user: tuple[str, int] | None,
    *,
    initial_skill_request: tuple[str, int] | None = None,
    initial_skill_body: tuple[str, int] | None = None,
) -> str | None:
    """構造と固定接頭辞だけで利用者介入ではない入力を分類する。

    接頭辞は、実行環境が利用者のメッセージへ挿入する本文、常駐処理の通知、及び定時promptの
    先頭に現れる固定文字列を実記録から採取したものとする。これらは利用者の発話ではないため、
    残すと利用者介入の候補が実際の介入件数を超える。
    接頭辞を持たない実行環境の生成は本文の形からは判別できないため、`_is_runtime_generated`が
    付けた標識で分類する。
    確認への回答のうち`answer_intervention`を持つものは、選択肢をそのまま選んだ回答ではなく
    従来の判断を是正した介入であるため、除外せず候補として残す。
    """
    if record != "main":
        return "delegated-record"
    if event.get("runtime_generated") is True:
        return "runtime-meta"
    if initial_skill_request == (record, line):
        return "initial-skill-request"
    if initial_skill_body == (record, line):
        return "initial-skill-body"
    if text.startswith("<skill>") and "</skill>" in text:
        return "runtime-inserted"
    if text.startswith(
        (
            "<system-reminder>",
            "[COMPACTION RECOVERY]",
            "This session is being continued",
            "<normative-context",
            "<agent-toolkit-auto-inserted",
            "<agent-toolkit-hook-message",
            "<task-notification>",
            "<command-name>",
            "<local-command-caveat>",
            "<local-command-stdout>",
            "A session-scoped Stop hook is now active",
            "Goal check-in:",
            "Stop hook feedback:",
        ),
    ):
        return "runtime-inserted"
    if text.startswith("質問:") and "回答:" in text:
        return None if event.get("answer_intervention") is True else "question-answer"
    if first_main_user == (record, line):
        return "initial-request"
    return None


def _initial_skill_input_locators(
    timeline: list[dict[str, Any]],
) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
    """Codexの先頭スキル要求と、直後に挿入された対応本文の位置を返す。"""
    main_users = [
        event
        for event in timeline
        if event.get("kind") == "user"
        and event.get("record") == "main"
        and isinstance(event.get("line"), int)
        and isinstance(event.get("text"), str)
        and event.get("runtime_generated") is not True
    ]
    if not main_users:
        return None, None
    request = main_users[0]
    request_text = str(request["text"]).strip()
    if not request_text.startswith("$") or any(character.isspace() for character in request_text):
        return None, None
    skill_name = request_text.removeprefix("$")
    if not skill_name or any(not (character.isalnum() or character in {"-", "_", ":"}) for character in skill_name):
        return None, None
    if len(main_users) < 2:
        return None, None
    body = main_users[1]
    body_text = str(body["text"]).lstrip()
    if not body_text.startswith("<skill>") or f"<name>{skill_name}</name>" not in body_text:
        return None, None
    return ("main", int(request["line"])), ("main", int(body["line"]))


def _is_help_command_failure(event: dict[str, Any]) -> bool:
    """変更処理へ到達せずusageを返した明示的なCLIヘルプ取得であるかを返す。"""
    command_value = event.get("command")
    diagnostic = event.get("diagnostic")
    if not isinstance(command_value, str) or not isinstance(diagnostic, str):
        return False
    try:
        command = json.loads(command_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(command, list) or not all(isinstance(argument, str) for argument in command):
        return False
    if not any(argument in {"-h", "--help"} for argument in command[1:]):
        return False
    normalized = diagnostic.casefold()
    return "usage:" in normalized or "options:" in normalized


def _is_normal_negative_result(event: dict[str, Any]) -> bool:
    """読取専用の述語が診断なしで偽を返した事象を区分する。"""
    if event.get("exit_code") != 1 or str(event.get("diagnostic", "")).strip():
        return False
    command_value = event.get("command")
    if not isinstance(command_value, str):
        return False
    try:
        args = json.loads(command_value)
    except json.JSONDecodeError:
        return False
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
        return False
    executable = Path(args[0]).name
    if executable in {"bash", "sh", "zsh"}:
        if len(args) < 3 or args[1] not in {"-c", "-lc"}:
            return False
        try:
            args = shlex.split(args[2])
        except ValueError:
            return False
        if not args or any(token in {";", "&&", "||", "|", ">", "<"} for token in args):
            return False
        executable = Path(args[0]).name
    if executable in {"rg", "grep", "git-grep"}:
        return True
    if executable in {"test", "["}:
        return any(flag in args for flag in ("-e", "-f", "-d", "-L"))
    if executable == "command":
        return len(args) >= 3 and args[1] == "-v"
    if executable == "cmp":
        return "-s" in args or "--silent" in args
    if executable != "git":
        return False
    git_args = args[1:]
    while git_args and git_args[0] == "-C" and len(git_args) >= 2:
        git_args = git_args[2:]
    if not git_args:
        return False
    if git_args[0] == "grep":
        return True
    return git_args[0] == "merge-base" and "--is-ancestor" in git_args[1:]


def _same_hook_event(candidate_kind: str, event: dict[str, Any], notice: dict[str, Any]) -> bool:
    """同じ記録位置の警告又は失敗が構造化hook通知から生じたかを返す。"""
    text = event.get("text")
    notice_text = notice.get("text")
    if not isinstance(text, str) or not isinstance(notice_text, str):
        return False
    if candidate_kind == "warning":
        normalized = " ".join(text.split())
        return bool(normalized) and normalized in " ".join(notice_text.split())
    hook_name = notice.get("hook_name")
    return isinstance(hook_name, str) and bool(hook_name) and text.startswith(hook_name) and "hook error:" in text


def _is_bounded_hook_group(key: tuple[str, ...]) -> bool:
    """発生源ごとの上位種への限定を適用する候補キーかを返す。

    対象はblock又はwarnのhook通知とする。他の種別のキーは軸の数が異なるため、
    タグの位置を参照する前に種別と軸の数を確認する。
    """
    return len(key) > 3 and key[0] == "hook-notice" and key[3] in {"block", "warn"}


def _candidate_mechanism(candidate_kind: str, event: dict[str, Any]) -> str:
    """定型接頭辞の後にある原因を候補キーの追加軸として返す。"""
    raw = event.get("text")
    if not isinstance(raw, str):
        return ""
    if candidate_kind == "escalation":
        reason = next((line.partition(":")[2].strip() for line in raw.splitlines() if line.startswith("reason:")), "")
    elif candidate_kind == "hook-notice" and event.get("tag") == "block":
        marker = re.search(r"\b(?:blocked|block):\s*", raw, flags=re.IGNORECASE)
        detail = raw[marker.end() :].strip() if marker else ""
        reason = detail.splitlines()[0].split("。", 1)[0].strip() if detail else ""
    else:
        return ""
    return _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(reason.split()))


def _candidate_key(candidate_kind: str, event: dict[str, Any], normalized_text: str) -> tuple[str, ...]:
    """候補種別ごとの正規化軸を、並べ替え可能な文字列tupleで返す。

    軸には原則として正規化した本文だけを置く。診断を欠くCommandExecutionでは本文による原因の
    区別が成立しないため、構造化コマンド、さらにコマンドも無い場合は記録位置を用いる。
    """
    if candidate_kind == "hook-notice":
        tag = str(event.get("tag", ""))
        return (
            candidate_kind,
            str(event.get("hook", "")),
            "" if tag in {"block", "warn"} else str(event.get("hook_name", "")),
            tag,
            _normalize_hook_candidate_text(normalized_text),
            _candidate_mechanism(candidate_kind, event),
        )
    if candidate_kind == "command-failure":
        if event.get("tool") == "CommandExecution":
            diagnostic = event.get("diagnostic")
            first_diagnostic_line = (
                diagnostic.splitlines()[0] if isinstance(diagnostic, str) and diagnostic.splitlines() else ""
            )
            command_or_location = str(event.get("command", "")) or (
                f"{event.get('record', '')}:{event.get('line', '')}" if not first_diagnostic_line else ""
            )
            return (
                candidate_kind,
                "CommandExecution",
                str(event.get("exit_code", "")),
                str(event.get("executable", "")),
                _normalize_candidate_kind_text(first_diagnostic_line) if first_diagnostic_line else command_or_location,
            )
        raw_text = event.get("text")
        first_line = raw_text.splitlines()[0] if isinstance(raw_text, str) and raw_text.splitlines() else ""
        return candidate_kind, _normalize_candidate_kind_text(first_line)
    if candidate_kind == "tool-failure":
        raw_text = event.get("text")
        if isinstance(raw_text, str) and "hook error:" in raw_text:
            source, _, body = raw_text.partition("hook error:")
            normalized_body = _HOOK_FAILURE_PREFIX.sub("", raw_text, count=1)
            if normalized_body == raw_text:
                normalized_body = body.strip()
            normalized_body = _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(normalized_body.split()))
            return candidate_kind, source.strip(), str(event.get("kind", "")), normalized_body
        diagnostic = (
            _CANDIDATE_VARIABLE.sub(_CANDIDATE_VARIABLE_PLACEHOLDER, " ".join(_EXIT_CODE_PREFIX.sub("", raw_text).split()))
            if isinstance(raw_text, str)
            else ""
        )
        return candidate_kind, str(event.get("tool_name", "")), str(event.get("operation", "")), diagnostic
    if candidate_kind == "escalation":
        return candidate_kind, _normalize_candidate_kind_text(normalized_text), _candidate_mechanism(candidate_kind, event)
    return candidate_kind, _normalize_candidate_kind_text(normalized_text)


def _bundle_timeline_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """通常表示を、イベント種別ごとの件数と問題候補の位置へ要約する。

    `assistant`と`skill-invocation`は件数だけで候補を確定できるため、本文を標準出力へ含めない。
    位置を伴う種別の本文も冒頭に限り、全体は`--detail`で取得する。
    位置を伴うイベントへは、そのエントリの時刻を`timestamp`として載せる。
    時刻を持たないエントリでは空文字列とし、所要時間の区間の境界を`--detail`の追加照会なしで確定できるようにする。
    """
    counts = collections.Counter(str(event["kind"]) for event in timeline)
    events: list[dict[str, Any]] = [
        {"kind": "bundle-kind-count", "event_kind": event_kind, "count": count}
        for event_kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    for event in timeline:
        event_kind = event["kind"]
        timestamp = event.get("timestamp")
        if event_kind in _BUNDLE_BODY_KINDS:
            events.append(
                {
                    "kind": "bundle-locator",
                    "event_kind": event_kind,
                    "record": event["record"],
                    "line": event["line"],
                    "timestamp": timestamp,
                    "text": _clip(str(event.get("text", "")), _BUNDLE_BODY_LENGTH),
                }
            )
        elif event_kind in _BUNDLE_LOCATOR_ONLY_KINDS:
            events.append(
                {
                    "kind": "bundle-locator",
                    "event_kind": event_kind,
                    "record": event["record"],
                    "line": event["line"],
                    "timestamp": timestamp,
                }
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


def _catalog_session_id(path: Path, records: list[_Record], runtime: _Runtime) -> str:
    """カタログ内記録のセッション識別子を返す。"""
    if runtime == "claude":
        return path.stem
    item = _CollectedRecord("main", path, records, runtime, None, None, None, "main")
    return _codex_record_thread_id(item) or path.stem.removeprefix("rollout-")


def _catalog_value(records: list[_Record], *keys: str) -> str:
    """セッションmetadataの先頭の非空文字列を返す。"""
    for record in records:
        entry = record.entry
        payload = entry.get("payload")
        for source in (entry, payload if isinstance(payload, dict) else {}):
            for key in keys:
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
        if isinstance(payload, dict):
            git = payload.get("git")
            if isinstance(git, dict):
                for key in keys:
                    value = git.get(key)
                    if isinstance(value, str) and value:
                        return value
    return "unknown"


def _wi_command(tokens: list[str]) -> str | None:
    """直接実行された`atk wi`の操作名を返す。"""
    index = 0
    while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
        index += 1
    words = tokens[index:]
    if len(words) < 3 or _basename(words[0]) != "atk" or words[1] != "wi":
        return None
    return words[2]


def _successful_wi_operations(item: _CollectedRecord) -> list[dict[str, Any]]:
    """成功結果まで記録された直接の`atk wi`操作を返す。"""
    pending: dict[str, tuple[int, str]] = {}
    operations: list[dict[str, Any]] = []
    for record in item.records:
        entry = record.entry
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    block_input = block.get("input")
                    command = block_input.get("command") if isinstance(block_input, dict) else None
                    operation = _wi_command(_shell_tokens(command)) if isinstance(command, str) else None
                    if operation is not None:
                        pending[block["id"]] = (record.line, operation)
                if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    call = pending.pop(block["tool_use_id"], None)
                    if call is not None and block.get("is_error") is not True:
                        line, operation = call
                        operations.append({"operation": operation, "locator": {"record": item.record_id, "line": line}})

        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if payload_type in {"function_call", "custom_tool_call"} and isinstance(payload.get("call_id"), str):
            operation = _wi_command(_payload_command_tokens(payload))
            if operation is not None:
                pending[payload["call_id"]] = (record.line, operation)
        elif payload_type in {"function_call_output", "custom_tool_call_output"} and isinstance(payload.get("call_id"), str):
            call = pending.pop(payload["call_id"], None)
            output = payload.get("output")
            output_object = output if isinstance(output, dict) else _json_object(output)
            failed = isinstance(output_object, dict) and output_object.get("exit_code") not in (None, 0)
            if call is not None and not failed:
                line, operation = call
                operations.append({"operation": operation, "locator": {"record": item.record_id, "line": line}})
        elif entry.get("type") == "event_msg" and payload_type == "item_completed":
            command_item = payload.get("item")
            if not isinstance(command_item, dict) or command_item.get("type") != "CommandExecution":
                continue
            operation = _wi_command(_payload_command_tokens(payload))
            if operation is not None and command_item.get("status") in {"completed", "success", "succeeded"}:
                operations.append({"operation": operation, "locator": {"record": item.record_id, "line": record.line}})
    return operations


def _workflow_evidence(item: _CollectedRecord) -> tuple[str, dict[str, Any] | str]:
    """明示されたprocess-wi系workflowと位置を返す。"""
    for event in _extract_records(item.records):
        text = event.get("text")
        if event.get("kind") == "skill-invocation" and isinstance(text, str) and "process-wi" in text:
            return "process-wi", {"record": item.record_id, "line": int(event["line"])}
        if (
            event.get("kind") == "user"
            and isinstance(text, str)
            and ("agent-toolkit:process-wi" in text or text.strip() == "/process-wi")
        ):
            return "process-wi", {"record": item.record_id, "line": int(event["line"])}
    return "unknown", "unknown"


def _catalog_family_tokens(family: list[_CollectedRecord]) -> dict[str, int] | str:
    """親とroot内で追跡できた子孫のトークンを同じ成分ごとに合算する。"""
    total: dict[str, int] = {}
    found = False
    for item in family:
        if item.runtime is None:
            continue
        tokens = _stats_summary_data(item.records, item.runtime).get("tokens")
        if not isinstance(tokens, dict):
            continue
        found = True
        _add_tokens(total, {key: value for key, value in tokens.items() if isinstance(value, int)})
    return total if found else "unknown"


def _catalog_events(
    root: Path,
    runtime: _Runtime,
    since: datetime.datetime,
    boundary: datetime.datetime,
) -> tuple[list[dict[str, Any]], int]:
    """指定root内だけから比較用の親セッションカタログを生成する。"""
    if not root.is_dir():
        return [{"kind": "error", "text": f"カタログrootが実在するディレクトリでない: {root}"}], 2
    resolved_root = root.resolve()
    paths = sorted(resolved_root.glob("**/*.jsonl"))
    if runtime == "codex":
        paths = [path for path in paths if path.name.startswith("rollout-")]
    if not paths:
        return [{"kind": "error", "text": f"カタログrootから{runtime}記録を判別できない: {resolved_root}"}], 2
    loaded: dict[str, _CollectedRecord] = {}
    path_items: dict[Path, _CollectedRecord] = {}
    unresolved_loads = 0
    for path in paths:
        records = _load_records(str(path))
        if records is None:
            unresolved_loads += 1
            continue
        detected = _detect_runtime([record.entry for record in records])
        if detected != runtime:
            continue
        session_id = _catalog_session_id(path, records, runtime)
        item = _CollectedRecord(session_id, path, records, runtime, None, None, None, "main")
        loaded.setdefault(session_id, item)
        path_items[path.resolve()] = item
    if not loaded:
        return [{"kind": "error", "text": f"カタログrootから{runtime}記録を判別できない: {resolved_root}"}], 2

    children: dict[str, list[str]] = {session_id: [] for session_id in loaded}
    referenced: set[str] = set()
    unresolved_references: set[tuple[str, str]] = set()
    for session_id, item in loaded.items():
        call_ids = _agents_server_call_ids(item.records)
        for record in item.records:
            for _, child_id in _thread_ids_from_record(record, call_ids):
                if child_id in loaded:
                    if child_id not in children[session_id]:
                        children[session_id].append(child_id)
                    referenced.add(child_id)
                else:
                    unresolved_references.add((session_id, child_id))
        if runtime == "claude":
            subagent_dir = item.path.with_suffix("") / "subagents"
            for path, child in path_items.items():
                if path.parent == subagent_dir.resolve() and child.record_id != session_id:
                    if child.record_id not in children[session_id]:
                        children[session_id].append(child.record_id)
                    referenced.add(child.record_id)

    if runtime == "claude":
        parent_ids = [
            item.record_id
            for path, item in path_items.items()
            if path.parent == resolved_root and item.record_id not in referenced
        ]
    else:
        parent_ids = [session_id for session_id in loaded if session_id not in referenced]

    events: list[dict[str, Any]] = []
    for parent_id in sorted(set(parent_ids)):
        parent = loaded[parent_id]
        stats = _stats_summary_data(parent.records, runtime)
        start_text = stats.get("start")
        end_text = stats.get("end")
        start = _parse_timestamp(start_text) if isinstance(start_text, str) else None
        end = _parse_timestamp(end_text) if isinstance(end_text, str) else None
        if end is not None and end <= since:
            continue
        if start is not None and start > boundary:
            continue
        descendant_ids: list[str] = []
        queue = list(children[parent_id])
        while queue:
            child_id = queue.pop(0)
            if child_id in descendant_ids:
                continue
            descendant_ids.append(child_id)
            queue.extend(children.get(child_id, ()))
        family = [parent, *(loaded[child_id] for child_id in descendant_ids)]
        workflow, workflow_locator = _workflow_evidence(parent)
        operations = [operation for item in family for operation in _successful_wi_operations(item)]
        events.append(
            {
                "kind": "catalog-parent",
                "runtime": runtime,
                "session_id": parent_id,
                "started_at": start_text if isinstance(start_text, str) else "unknown",
                "finished_at": end_text if isinstance(end_text, str) else "unknown",
                "cwd": _catalog_value(parent.records, "cwd", "originalCwd"),
                "branch": _catalog_value(parent.records, "gitBranch", "originalBranch", "branch"),
                "workflow": workflow,
                "workflow_locator": workflow_locator,
                "tokens": _catalog_family_tokens(family),
                "descendant_count": len(descendant_ids),
                "successful_wi_operations": operations,
                "successful_wi_operation_count": len(operations),
            }
        )
    events.sort(key=lambda event: (str(event["started_at"]), str(event["session_id"])))
    events.append(
        {
            "kind": "catalog-summary",
            "scan_root": str(resolved_root),
            "runtime": runtime,
            "since": since.isoformat(),
            "observation_boundary": boundary.isoformat(),
            "parent_record_count": len(events),
            "unresolved_record_count": unresolved_loads + len(unresolved_references),
        }
    )
    return events, 0


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
        "--transcript",
        metavar="PATH",
        help="位置引数と同じ単一transcriptの絶対パス。位置引数・Codex thread ID・カタログ走査とは併用しない。",
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
    catalog_group = parser.add_mutually_exclusive_group()
    catalog_group.add_argument(
        "--catalog-claude-project",
        metavar="DIR",
        help="指定したClaude projectディレクトリ内だけを走査し、比較用の親セッションカタログを返す。"
        "`--since`と`--observation-boundary`が必須。",
    )
    catalog_group.add_argument(
        "--catalog-codex-history",
        metavar="DIR",
        help="指定したCodex履歴ディレクトリ内だけを走査し、比較用の親セッションカタログを返す。"
        "`--since`と`--observation-boundary`が必須。",
    )
    parser.add_argument(
        "--compaction-record-dir",
        metavar="DIR",
        help="Codexコンパクションの計測記録ディレクトリ。省略時はagent-toolkitのstate_dir配下を使う。",
    )
    parser.add_argument(
        "--observation-boundary",
        metavar="TIMESTAMP",
        help="ISO 8601の時刻を観測境界とし、親記録のうち当該時刻より後の`timestamp`を持つレコードを"
        "全モードの対象外にする。委譲先の記録へは適用しない。`--detail`の行番号は元ファイルの行番号を維持する。"
        "解析できない値はエラーイベントを出力して終了コード2を返す。",
    )
    parser.add_argument(
        "--elapsed-until",
        metavar="TIMESTAMP",
        help="ISO 8601の時刻を経過時間の終端とし、メイン記録の最初のレコードから当該時刻までの経過秒数を返す。"
        "解析できない値、算出できる記録が無い場合及び当該時刻が最初のレコードより前の場合は"
        "エラーイベントを出力して終了コード2を返す。",
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
        "一致行と一致エントリ数を照会する。各一致行は元記録行の時刻`timestamp`（無ければnull）を持つ。",
    )
    parser.add_argument(
        "--detail",
        action="append",
        default=None,
        metavar="RECORD:LINE",
        help="指定した<記録>:<行番号>（数値だけならメイン記録）のエントリの詳細（tool_useの入力全体・tool_result本文。"
        "本文が退避されている場合はツール実行結果側の本文）を照会する。複数指定ではオプションを繰り返す。"
        "各イベントは元記録行の時刻`timestamp`（無ければnull）を持つ。"
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
        "--user-events",
        action="store_true",
        help="`--since`より後から観測境界までのメイン記録にある利用者イベントだけを照会する。`--since`が必須。",
    )
    parser.add_argument(
        "--since",
        metavar="TIMESTAMP",
        help="`--user-events`又はカタログ走査の開始境界をISO 8601の時刻で指定する。",
    )
    parser.add_argument(
        "--bundle",
        metavar="DIR",
        help="通常表示、`--warn`、`--stats`及び`--hook-notices`の走査を1回の記録読み込みで行い、"
        "走査ごとの全量を指定したディレクトリ配下のファイルへ書く。"
        "標準出力へは、走査ごとのファイルの絶対パスとイベント件数、通常表示のイベント種別ごとの件数、"
        "問題候補の特定に用いるイベントの位置と本文の冒頭、及び警告の種別ごとの件数を返す。"
        "集計と通知の走査の全量は保存先のファイルから読む。"
        "指定するディレクトリは実在していることを要する。他の照会オプションとは併用しない。",
    )
    parser.add_argument(
        "--output-file",
        metavar="PATH",
        help="全てのモードの標準出力を指定した絶対パスのファイルへ保存し、標準出力へは保存先パスと保存した行数だけを書く。",
    )
    return parser


def main(argv: list[str] | None = None, *, _output_file_active: bool = False) -> int:
    """証拠または照会結果を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.output_file is not None and not _output_file_active:
        output_path = Path(args.output_file)
        if not output_path.is_absolute():
            return _print_error("--output-fileには絶対パスを指定してください。")
        resolved = output_path.resolve(strict=False)
        with resolved.open("w", encoding="utf-8", newline="") as stream, contextlib.redirect_stdout(stream):
            exit_code = main(argv, _output_file_active=True)
        with resolved.open(encoding="utf-8", newline="") as stream:
            line_count = sum(1 for _line in stream)
        print(f"保存先: {resolved}")
        print(f"行数: {line_count}")
        return exit_code
    if (
        sum(
            (
                args.warn,
                args.grep is not None,
                args.detail is not None,
                args.stats,
                args.hook_notices,
                args.bundle is not None,
                args.elapsed_until is not None,
                args.user_events,
            )
        )
        > 1
    ):
        return _print_error("--warn・--grep・--detail・--stats・--hook-notices・--bundle・--elapsed-untilは併用できない")
    catalog_root = args.catalog_claude_project or args.catalog_codex_history
    catalog_runtime: _Runtime | None = (
        "claude" if args.catalog_claude_project else "codex" if args.catalog_codex_history else None
    )
    if catalog_root is not None and any(
        (
            args.warn,
            args.grep is not None,
            args.detail is not None,
            args.stats,
            args.hook_notices,
            args.bundle is not None,
            args.elapsed_until is not None,
            args.user_events,
        )
    ):
        return _print_error("カタログ走査は単一transcriptの照会モードと併用できない")
    if args.since is not None and not args.user_events and catalog_root is None:
        return _print_error("--sinceは--user-events又はカタログ走査と併用する")
    if args.user_events and args.since is None:
        return _print_error("--user-eventsには--sinceが必要")
    if catalog_root is not None and args.since is None:
        return _print_error("カタログ走査には--sinceが必要")
    if catalog_root is not None and args.observation_boundary is None:
        return _print_error("カタログ走査には--observation-boundaryが必要")
    since = None
    if args.since is not None:
        try:
            since = _parse_timestamp(args.since)
        except ValueError:
            return _print_error(f"開始境界が不正: {args.since}")

    sources = (
        args.transcript_path,
        args.transcript,
        args.codex_thread_id,
        args.catalog_claude_project,
        args.catalog_codex_history,
    )
    if sum(source is not None for source in sources) != 1:
        return _print_error("transcript_path・--transcript・--codex-thread-id・カタログ走査はいずれか一つだけを指定する")
    if args.codex_home is not None and args.codex_thread_id is None:
        return _print_error("--codex-homeは--codex-thread-idと併用する")
    if catalog_root is not None:
        assert since is not None and catalog_runtime is not None and args.observation_boundary is not None
        try:
            catalog_boundary = _parse_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}")
        if catalog_boundary < since:
            return _print_error("観測境界は開始境界以後を指定する")
        events, exit_code = _catalog_events(Path(catalog_root), catalog_runtime, since, catalog_boundary)
        _print_events(events)
        return exit_code
    if args.codex_thread_id is not None:
        try:
            transcript_path = str(_resolve_codex_transcript(args.codex_thread_id, args.codex_home))
        except ValueError as error:
            return _print_error(str(error))
    else:
        transcript_path = args.transcript or args.transcript_path

    records = _load_records(transcript_path)
    if records is None:
        return _print_error(f"対象記録を読み込めない: {transcript_path}")
    boundary = None
    if args.observation_boundary is not None:
        try:
            boundary = _parse_timestamp(args.observation_boundary)
        except ValueError:
            return _print_error(f"観測境界が不正: {args.observation_boundary}")
    if args.elapsed_until is not None:
        event = _elapsed_until_event(records, args.elapsed_until)
        if isinstance(event, str):
            message = event or f"経過時間を算出できる記録が無い: {transcript_path}"
            return _print_error(message)
        _print_events([event])
        return 0
    if boundary is not None:
        records = _apply_observation_boundary(records, boundary)
    delegate_codex_home = args.codex_home if args.codex_thread_id is not None else None
    collected, unresolved = _collect_records(transcript_path, records, delegate_codex_home, boundary)
    compaction_record_dir = (
        Path(args.compaction_record_dir)
        if args.compaction_record_dir is not None
        else _atk_config.state_dir() / "agents-server" / "compaction"
    )

    if args.bundle is not None:
        events, exit_code = _bundle_events(collected, unresolved, Path(args.bundle), compaction_record_dir)
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
        _print_events([*_stats_events(collected, compaction_record_dir), *_unresolved_events(unresolved)])
        return 0
    if args.hook_notices:
        _print_events(
            [*_hook_notice_events([record for item in collected for record in item.records]), *_unresolved_events(unresolved)]
        )
        return 0
    if args.user_events:
        assert since is not None
        _print_events(_user_events_since(collected, since))
        return 0

    _print_events(_default_events(collected, unresolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
