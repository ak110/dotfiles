from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import time

from agent_toolkit._common import response_language_check as _response_language_check
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks import agent_id as _agent_id
from agent_toolkit._hooks.pretooluse.content_checks import _warn_mojibake
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.notices import _block_notice
from agent_toolkit._hooks.pretooluse.warning_context import format_warning_context
lazy from agent_toolkit._atk.wi import cli, entries
lazy from agent_toolkit._common import private_notes
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._hooks.norm_excuse_gate import warning_for_texts
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._plan.owner_records import resolve_conversation_session_id

USER_FACING_TEXT_TOOL_NAMES: frozenset[str] = frozenset({"AskUserQuestion", "ExitPlanMode"})
_POLICY_APPROVAL_QUESTION = re.compile(r"送った(?P<modifier>[^\n]*?)方針で進めて(?:よい|いい)ですか[？?]")

_APPROVAL_STATE = "policy_reapproval"
_APPROVAL_TOOLS = {"AskUserQuestion", "request_user_input", "request_user_input_async"}
_ASYNC_QUESTION = "request_user_input_async"
_APPROVED = "この方針で進める"


def _policy_data(state: dict, session_id: str, *, create: bool = False) -> dict:
    data = state.get(_APPROVAL_STATE)
    if isinstance(data, dict) and data.get("owner") == session_id:
        return data
    # pyfltr: allow-comment -- transcriptから共通状態を継承しても、再承認は元の会話だけに属する。
    data = {"owner": session_id} if create else {}
    if create:
        state[_APPROVAL_STATE] = data
    return data


def record_policy_question(payload: dict, session_id: str, tool_name: str, tool_input: dict) -> None:
    call_id = payload.get("tool_use_id")
    if tool_name not in _APPROVAL_TOOLS or not isinstance(call_id, str) or not call_id:
        return
    if not _agent_id.is_main_agent_context(payload):
        return
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or len(questions) != 1 or not isinstance(questions[0], dict):
        return
    question = questions[0]
    text = question.get("title" if tool_name == _ASYNC_QUESTION else "question")
    match = _POLICY_APPROVAL_QUESTION.search(text) if isinstance(text, str) else None
    if match is None:
        return
    key = tool_input["questions"][0].get("id") if tool_name == "request_user_input" else text
    modifier = match["modifier"]

    def update(state: dict) -> dict:
        data = _policy_data(state, session_id, create=True)
        data.setdefault("questions", {}).setdefault(call_id, [tool_name, modifier, key, time.time_ns(), False])
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
    corrected = data.setdefault("corrected", {})
    if answer != _APPROVED:
        waits[modifier] = None
        corrected[modifier] = time.time_ns()
    elif modifier in waits and corrected.get(modifier, asked) < asked:
        del waits[modifier]
        corrected.pop(modifier, None)


def observe_policy_result(payload: dict, session_id: str, tool_name: str, _tool_input: dict) -> None:
    if tool_name not in _APPROVAL_TOOLS or not _agent_id.is_main_agent_context(payload):
        return
    response = _result_object(payload.get("tool_response"))
    call_id = payload.get("tool_use_id")

    def update(state: dict) -> dict | None:
        data = _policy_data(state, session_id)
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
        data = _policy_data(state, session_id)
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
                or not isinstance(index := identity[2], int)
                or isinstance(index, bool)
                or index != 0
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
    waits = _policy_data(read_state(owner), owner).get("waits")
    if not waits:
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
                    "修正を反映した方針と適用対象を示して承認を問い直し、同じ対象への実回答を得てください。"
                    "発行の成功や無回答は承認に数えません。"
                    if main
                    else "委譲元が同じ対象への再承認を得るまで確定できません。結果を委譲元へ返して待ってください。"
                ),
            )
    return None


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
    if fields:
        session_id = _payload.get("session_id")
        if warning := warning_for_texts(
            (text for _, text in fields),
            session_id if isinstance(session_id, str) else "",
            event_id=_payload.get("tool_use_id"),
        ):
            warnings.append(warning)
    return Decision(context=format_warning_context(warnings) if warnings else None)
