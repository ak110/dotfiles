from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import time
from collections.abc import Iterable

lazy from markdown_it import MarkdownIt
lazy from markdown_it.token import Token

from agent_toolkit._common import external_source_review
from agent_toolkit._common import response_language_check as _response_language_check
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks import agent_id as _agent_id
from agent_toolkit._hooks import bugfix_gate, norm_excuse_gate, source_review_receipt, work_contract
from agent_toolkit._hooks import notice as _notice
from agent_toolkit._hooks.pretooluse.content_checks import _warn_mojibake
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.notices import _block_notice
from agent_toolkit._hooks.pretooluse.warning_context import format_warning_context
lazy from agent_toolkit._atk.wi import cli, entries
lazy from agent_toolkit._common import private_notes
lazy from agent_toolkit._common.markdown_headings import without_literal_blocks
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._plan.owner_records import resolve_conversation_session_id

USER_FACING_TEXT_TOOL_NAMES: frozenset[str] = frozenset({"AskUserQuestion", "ExitPlanMode"})
_MARKDOWN_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+)$")
_STRONG_HEADING = re.compile(r"^\s*(\*\*|__)([^:：]+?)([:：]?)\1\s*([:：]?)(.*)$")
_UNDERSTANDING_HEADING = re.compile(r"^発話の理解\s*(?:[:：]\s*(.*)|$)")
_ORIGINAL_TEXT_LABEL = re.compile(r"^\s*(?:[-*+]\s+)?[*_]*(?:原文|[^:：\n]*?[（(]原文[）)])[*_]*\s*(?:[:：]|$)")


_ALTERNATIVE_LABEL = re.compile(r"[^\S\n]*(?:[-*+][^\S\n]+)?[*_]*別案(?:[*_]*[^\S\n]*[:：]|[:：][*_]+)")
_INLINE_ALTERNATIVE_LABEL = re.compile(r"[^\S\n]*[*_]*別案[*_]*[^\S\n]*[:：]")
_ALTERNATIVE_BOUNDARY = re.compile(r"[「」『』。\n]")
_QUOTE_END = {"「": "」", "『": "』"}
_POLICY_APPROVAL = re.compile(r"方針[^？?\n]{0,80}で(?:進めて|起草して|実施して)(?:よい|いい)")
SOURCE_REVIEW_FILE = external_source_review.TABLE_NAME
_SOURCE_REVIEW_WORK = re.compile(r"^\s*-?\s*作業[:：]\s*`?([^\s`]+)`?\s*$")


def _understanding_section_lines(message: str) -> Iterable[tuple[str, bool]]:
    section_level = 0
    plain_paragraph = False
    paragraph_break = False
    for line in without_literal_blocks(message).splitlines():
        heading = _MARKDOWN_HEADING.match(line)
        strong = _STRONG_HEADING.match(line)
        if strong and strong[5] and not (strong[3] or strong[4]):
            strong = None
        if heading:
            level = len(heading[1])
            title = heading[2].strip(" #*_")
        elif strong:
            level = 6
            title = strong[2].strip() + (strong[3] or strong[4]) + strong[5]
        else:
            level = 0
            title = line.strip()
        if (target := _UNDERSTANDING_HEADING.fullmatch(title)) is not None:
            section_level = level or 6
            paragraph_break = False
            plain_paragraph = not level and target[1] is not None
            if target[1] or plain_paragraph:
                yield target[1] or "", plain_paragraph
            continue
        if (heading and level <= section_level) or (plain_paragraph and not line.strip()):
            section_level = 0
            continue
        if not section_level:
            continue
        if (
            strong
            and (paragraph_break or not strong[5].strip())
            and not (_ORIGINAL_TEXT_LABEL.match(line) or _ALTERNATIVE_LABEL.match(line))
        ):
            section_level = 0
            continue
        paragraph_break = not line.strip()
        yield line, plain_paragraph


def _without_heading_marker(line: str) -> str:
    return heading[2].strip(" #*_") if (heading := _MARKDOWN_HEADING.match(line)) else line


def has_original_text_label_in_understanding(message: str) -> bool:
    return any(_ORIGINAL_TEXT_LABEL.match(_without_heading_marker(line)) for line, _ in _understanding_section_lines(message))


def _alternative_text(token: Token) -> str:
    if token.type == "text":
        return token.content
    if token.type in {"softbreak", "hardbreak"}:
        return "\n"
    if token.type in {"em_open", "em_close", "strong_open", "strong_close"}:
        return token.markup
    return "\0"


def _has_alternative(lines: list[str]) -> bool:
    text = "\n".join(lines)
    if "`" in text:
        text = "\n".join(
            "".join(_alternative_text(child) for child in block.children or [])
            for block in MarkdownIt("commonmark").parse(text)
            if block.type == "inline"
        )
    else:
        text = "\n".join(map(_without_heading_marker, lines))
    if _ALTERNATIVE_LABEL.match(text):
        return True
    quote_ends: list[str] = []
    for boundary in _ALTERNATIVE_BOUNDARY.finditer(text):
        mark = boundary[0]
        if mark in _QUOTE_END:
            quote_ends.append(_QUOTE_END[mark])
        elif quote_ends:
            if mark == quote_ends[-1]:
                quote_ends.pop()
        elif (
            mark == "。"
            and _INLINE_ALTERNATIVE_LABEL.match(text, boundary.end())
            or mark == "\n"
            and _ALTERNATIVE_LABEL.match(text, boundary.end())
        ):
            return True
    return False


def missing_alternative(message: str) -> bool:
    section = list(_understanding_section_lines(message))
    lines = [line for line, _ in section if not bugfix_gate.is_classification_line(_without_heading_marker(line))]
    required = sum(bool(line.strip()) for line in lines) > 1 or any(plain for _, plain in section)
    return required and not _has_alternative(lines)


def external_source_review_needed(session_id: str, tool_input: dict, *, cwd: str | None = None) -> str | None:
    fields = _user_facing_text_fields("AskUserQuestion", tool_input)
    if not any(
        field.endswith((".header", ".question")) and (value == "方針の承認" or _POLICY_APPROVAL.search(value))
        for field, value in fields
    ):
        return None
    contract = work_contract.record_path(session_id)
    try:
        body = contract.read_text(encoding="utf-8") if contract is not None else ""
    except OSError, UnicodeError:
        body = ""
    lines = without_literal_blocks(body).splitlines()
    entry = read_state(session_id).get(work_contract.EXTERNAL_SOURCE_ENTRY_KEY)
    if isinstance(entry, dict):
        if entry.get("work") not in work_contract.EXTERNAL_SOURCE_WORKS:
            return None
        recorded = entry.get("urls")
    elif any(
        (match := _SOURCE_REVIEW_WORK.fullmatch(line)) and match[1] in work_contract.EXTERNAL_SOURCE_WORKS for line in lines
    ):
        recorded = None
    else:
        return None
    directory = pathlib.Path(cwd).resolve() if cwd else pathlib.Path.cwd()
    root = next((path for path in (directory, *directory.parents) if (path / ".git").exists()), directory)
    urls = external_source_review.contract_urls(body, recorded)
    if error := external_source_review.check_table(contract, urls, root):
        return error
    if urls and not source_review_receipt.ready(session_id):
        return "`agent-basics:writing-standards/references/design-heuristics.md`を全文読んでください。"
    return None


_POLICY_APPROVAL_HEADER = "方針の承認"
POLICY_APPROVAL_MODIFIER_LIMIT = 6
_POLICY_APPROVAL_QUESTION = re.compile(
    rf"送った(?P<modifier>[^\W_]{{0,{POLICY_APPROVAL_MODIFIER_LIMIT}}})方針で進めてよいですか[？?]"
)
_POLICY_APPROVAL_CLAUSE = re.compile(
    r"を|(?:[一-龯々][ぁ-ゖ]*(?:[うくぐすつぶむるただ]|ない|ず|ぬ)|しない|せ[ずぬ]|(?:ら?れ|でき)[るた])(?:版)?の?$"
)
_POLICY_APPROVAL_OPTIONS = (
    {"label": "この方針で進める", "description": "送った方針のとおりに着手する"},
    {"label": "修正して進める", "description": "修正点を自由記述で伝える"},
)


def policy_approval_form_invalid(tool_input: dict) -> bool:
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not any(
        isinstance(question, dict)
        and (
            question.get("header") == _POLICY_APPROVAL_HEADER
            or (isinstance(text := question.get("question"), str) and _POLICY_APPROVAL.search(text))
        )
        for question in questions
    ):
        return False
    if len(questions) != 1:
        return True
    question = questions[0]
    options = question.get("options")
    return not (
        question.get("header") == _POLICY_APPROVAL_HEADER
        and not question.get("multiSelect")
        and isinstance(text := question.get("question"), str)
        and (match := _POLICY_APPROVAL_QUESTION.fullmatch(text)) is not None
        and not _POLICY_APPROVAL_CLAUSE.search(match["modifier"])
        and isinstance(options, list)
        and len(options) == len(_POLICY_APPROVAL_OPTIONS)
        and all(
            isinstance(option, dict)
            and option.get("label") == expected["label"]
            and option.get("description") == expected["description"]
            and not option.get("preview")
            for option, expected in zip(options, _POLICY_APPROVAL_OPTIONS, strict=True)
        )
    )


_APPROVAL_STATE = "policy_reapproval"
_APPROVAL_TOOLS = {"AskUserQuestion", "request_user_input", "request_user_input_async"}
_ASYNC_QUESTION = "request_user_input_async"
_APPROVED = _POLICY_APPROVAL_OPTIONS[0]["label"]


def normalize_policy_question(tool_name: str, tool_input: dict) -> dict:
    if tool_name != _ASYNC_QUESTION or not isinstance(questions := tool_input.get("questions"), list):
        return tool_input
    normalized = []
    for question in questions:
        title = question.get("title") if isinstance(question, dict) else None
        if not isinstance(title, str) or not title.startswith("方針の承認："):
            normalized.append({})
            continue
        options = question.get("options")
        normalized.append(
            {
                "header": _POLICY_APPROVAL_HEADER,
                "question": title.removeprefix("方針の承認："),
                "options": list(_POLICY_APPROVAL_OPTIONS)
                if options == [item["label"] for item in _POLICY_APPROVAL_OPTIONS]
                else None,
            }
        )
    return {"questions": normalized}


def record_policy_question(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> None:
    call_id = payload.get("tool_use_id")
    if tool_name not in _APPROVAL_TOOLS or not isinstance(call_id, str) or not call_id:
        return
    if not _agent_id.is_main_agent_context(payload):
        return
    normalized = normalize_policy_question(tool_name, tool_input)
    questions = normalized.get("questions")
    if not isinstance(questions, list) or len(questions) != 1 or not isinstance(questions[0], dict):
        return
    question = questions[0]
    if question.get("header") != _POLICY_APPROVAL_HEADER or policy_approval_form_invalid(normalized):
        return
    match = _POLICY_APPROVAL_QUESTION.fullmatch(question["question"])
    if match is None:
        return
    key = tool_input["questions"][0].get("id") if tool_name == "request_user_input" else question["question"]

    def update(state: dict) -> dict:
        data = state.setdefault(_APPROVAL_STATE, {})
        data.setdefault("questions", {}).setdefault(call_id, [tool_name, match["modifier"], key, time.time_ns(), False])
        return state

    update_state(session_id, update)


def _result_object(response: object) -> dict:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return {}
    if isinstance(response, list) and len(response) == 1 and isinstance(response[0], dict):
        return _result_object(response[0].get("text"))
    if not isinstance(response, dict) or response.get("isError") is True:
        return {}
    structured = response.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = response.get("content")
    if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
        return _result_object(content[0].get("text"))
    return response


def _apply_policy_answer(data: dict, call_id: str, question: list, answer: str) -> None:
    _, modifier, _, asked, _ = question
    del data["questions"][call_id]
    waits = data.setdefault("waits", {})
    if answer != _APPROVED:
        waits[modifier] = None
    elif modifier in waits and waits[modifier] is not None and waits[modifier] < asked:
        del waits[modifier]


def observe_policy_result(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> None:
    delivery = tool_name == "mcp__agents_server__send_to_user"
    if (not delivery and tool_name not in _APPROVAL_TOOLS) or not _agent_id.is_main_agent_context(payload):
        return
    response = _result_object(payload.get("tool_response"))
    call_id = payload.get("tool_use_id")

    def update(state: dict) -> dict | None:
        data = state.get(_APPROVAL_STATE, {})
        if delivery:
            message = tool_input.get("message")
            if response.get("ok") is not True or not isinstance(message, str):
                return None
            first = message.split("\n", 1)[0]
            if not first.startswith("方針（案）"):
                return None
            modifier = first.removeprefix("方針（案）").strip()
            waits = data.get("waits", {})
            if modifier not in waits:
                return None
            waits[modifier] = time.time_ns()
            return state
        if not isinstance(call_id, str):
            return None
        question = data.get("questions", {}).get(call_id)
        if not question or question[0] != tool_name:
            return None
        if tool_name == _ASYNC_QUESTION:
            question[4] = True
            return state
        if response.get("afkTimeoutMs") is not None:
            return None
        answers = response.get("answers")
        answer = answers.get(question[2]) if isinstance(answers, dict) else None
        annotations = response.get("annotations")
        annotation = annotations.get(question[2]) if isinstance(annotations, dict) else None
        notes = annotation.get("notes") if isinstance(annotation, dict) else None
        if isinstance(notes, str) and notes.strip():
            answer = notes
        if tool_name == "request_user_input" and isinstance(answer, dict):
            values = answer.get("answers")
            answer = values[0] if isinstance(values, list) and len(values) == 1 else None
        if not isinstance(answer, str) or not answer.strip():
            answer = response.get("response")
        if not isinstance(answer, str) or not answer.strip():
            return None
        _apply_policy_answer(data, call_id, question, answer)
        return state

    update_state(session_id, update)


def observe_async_policy_reply(session_id: str, prompt: str) -> None:
    opening, closing = "<send_user_message_question_reply>", "</send_user_message_question_reply>"
    prompt = prompt.strip()
    if not prompt.startswith(opening) or not prompt.endswith(closing):
        return
    try:
        replies = json.loads(prompt[len(opening) : -len(closing)])
    except ValueError:
        return
    if not isinstance(replies, list):
        return

    def update(state: dict) -> dict | None:
        data = state.get(_APPROVAL_STATE, {})
        changed = False
        for reply in replies:
            if not isinstance(reply, dict) or not isinstance(item_id := reply.get("questionItemId"), str):
                continue
            try:
                identity = json.loads(item_id)
            except ValueError:
                continue
            if (
                not isinstance(identity, list)
                or len(identity) != 3
                or identity[0] != _ASYNC_QUESTION
                or not isinstance(call_id := identity[1], str)
                or type(identity[2]) is not int
                or identity[2] != 0
                or not isinstance(answer := reply.get("answer"), str)
            ):
                continue
            question = data.get("questions", {}).get(call_id)
            if question and question[0] == _ASYNC_QUESTION and question[4]:
                _apply_policy_answer(data, call_id, question, answer)
                changed = True
        return state if changed else None

    update_state(session_id, update)


def _saves_awi(tokens: tuple[str, ...]) -> bool:
    parser = argparse.ArgumentParser(exit_on_error=False)
    cli.build_parser(parser)
    try:
        args = parser.parse_args(tokens[2:])
        if args.dry_run:
            return False
        if tokens[2] == "add":
            return args.type != "uwi"
        root = private_notes.default_private_notes(pathlib.Path.home())
        names = entries.validate_named_filenames(root, [args.filename])
        records, _ = entries.read_named_entries(root, names, target_repo=None)
        return not records or any(record[-1] != "uwi" for record in records)
    except argparse.ArgumentError, SystemExit, OSError, ValueError:
        return True


def policy_reapproval_block(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str | None:
    command = tool_input.get("command")
    if tool_name not in {"Bash", "PowerShell", "exec_command", "functions.exec_command"} or not isinstance(command, str):
        return None
    main = _agent_id.is_main_agent_context(payload)
    owner = session_id if main else resolve_conversation_session_id() or session_id
    if not read_state(owner).get(_APPROVAL_STATE, {}).get("waits"):
        return None
    for segment in extract_execution_segments(command):
        tokens = segment.tokens
        git = git_subcommand_tokens(segment)
        if (git and git[0] == "commit" and not {"--help", "-h", "--dry-run"}.intersection(git[1])) or (
            segment.resolved
            and is_atk_command(tokens)
            and tokens[1:3] in {("wi", "add"), ("wi", "edit")}
            and _saves_awi(tokens)
        ):
            return _block_notice(
                "方針の修正回答に対する再承認を待っているため、確定操作を止めました。",
                fix=(
                    "同じ会話で、修正した方針を1行目「方針（案）」（修飾があれば続けて記載）の"
                    "send_to_userで送り、その後に同じ修飾の方針の承認を問い直してください。"
                    "送達できない場合は同じ会話でagents_serverの接続を回復してください（Claude Codeでは /mcp）。"
                    if main
                    else "委譲元が再送と再承認を行うまで確定できません。結果を委譲元へ返して待ってください。"
                ),
            )
    return None


def recommendation_review_needed(payload: dict, session_id: str, tool_input: dict) -> bool:
    if not session_id or not any(
        ".options[" in field and ("推奨" in value or re.search(r"\brecommended\b", value, re.IGNORECASE))
        for field, value in _user_facing_text_fields("AskUserQuestion", tool_input)
    ):
        return False
    return not _notice.consume_resend(
        session_id,
        _agent_id.resolve_hook_agent_id(payload),
        "recommendation_review_by_actor",
        json.dumps(tool_input, sort_keys=True, ensure_ascii=False),
    )


def _user_facing_text_fields(tool_name: str, tool_input: dict) -> list[tuple[str, str]]:
    if tool_name == "ExitPlanMode":
        plan = tool_input.get("plan")
        return [("plan", plan)] if isinstance(plan, str) else []
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        return []
    fields: list[tuple[str, str]] = []
    for question_index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        for name in ("question", "header"):
            value = question.get(name)
            if isinstance(value, str):
                fields.append((f"questions[{question_index}].{name}", value))
        options = question.get("options")
        if not isinstance(options, list):
            continue
        for option_index, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            for name in ("label", "description"):
                value = option.get(name)
                if isinstance(value, str):
                    fields.append((f"questions[{question_index}].options[{option_index}].{name}", value))
    return fields


def scope_review_text(tool_name: str, tool_input: dict) -> str:
    if tool_name == "AskUserQuestion":
        return "\n".join(value for _, value in _user_facing_text_fields(tool_name, tool_input))
    values = [tool_input.get(name) for name in ("message", "prompt", "instruction")]
    if isinstance(extra := tool_input.get("extra_params"), dict):
        values.extend(extra.values())
    return "\n".join(value for value in values if isinstance(value, str))


def language_retry(payload: dict, tool_name: str, tool_input: dict, *, remember: bool) -> bool:
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return False
    actor = _agent_id.resolve_hook_agent_id(payload)
    fingerprint = hashlib.sha256(json.dumps([tool_name, tool_input], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    repeated = False

    def update(state: dict) -> dict:
        nonlocal repeated
        prior = state.get("response_language_retry_by_actor", {})
        prior = dict(prior) if isinstance(prior, dict) else {}
        repeated = prior.get(actor) == fingerprint
        if remember:
            prior[actor] = fingerprint
        else:
            prior.pop(actor, None)
        state["response_language_retry_by_actor"] = prior
        return state

    return update_state(session_id, update) and repeated


def decide_user_facing_text_tool(_payload: dict, tool_name: str, tool_input: dict) -> Decision:
    # 記述言語だけは表示前に遮断する。「遮断と警告の選択」が定める例外で、文字化けは警告に留める。  # pyfltr: allow-comment
    fields = _user_facing_text_fields(tool_name, tool_input)
    if excuse_block := norm_excuse_gate.text_block(
        _payload, "\n".join(text for _, text in fields), "ユーザーへの質問・計画の提示"
    ):
        return Decision(block=excuse_block)
    recoverable = all(_response_language_check.explanation_is_japanese(text) for _, text in fields)
    repeated = language_retry(_payload, tool_name, tool_input, remember=recoverable)
    for name, text in fields:
        # `host-claude-code.md`「ユーザー確認（AskUserQuestion）」が求める選択肢の固定表記。  # pyfltr: allow-comment
        # 本文・説明の英語判定と文字化け判定には、この除外を適用しない。  # pyfltr: allow-comment
        if tool_name == "AskUserQuestion" and name.endswith(".label"):
            text = text.removesuffix("(Recommended)").rstrip()
        outcome, _ = _response_language_check.check_text(text)
        if outcome is _response_language_check.CheckOutcome.WARN:
            if recoverable and repeated:
                continue
            return Decision(
                block=_block_notice(
                    f"対象の入力欄: `{name}`。この入力本文を日本語の説明として判定できないため、呼び出しを止めた。",
                    fix=(
                        _response_language_check.WARNING_FIX
                        + "引用・入力素材だけが英語で、説明が日本語なら、同じ主体から同じツールへ同一入力を再発行してください。"
                    ),
                )
            )
    warnings = [warning for warning in (_warn_mojibake(tool_name, fields),) if warning is not None]
    return Decision(context=format_warning_context(warnings) if warnings else None)
