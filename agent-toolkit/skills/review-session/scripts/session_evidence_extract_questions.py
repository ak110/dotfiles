"""記録からの証拠イベント抽出（session_evidence_extract_questions）。"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from session_evidence_extract_common import _clip, _event, _OfferedOption

from agent_toolkit._common.session_family import _json_object

_CodexPendingQuestions = dict[str, tuple[int, dict[str, tuple[str, list[_OfferedOption]]]]]
"""Codexのcall_idごとの質問行と質問文・選択肢。非同期質問のキーはquestionItemIdのJSON配列とする。"""


class _AnsweredQuestion(NamedTuple):
    """回答イベントへ書く1問分の質問文、提示した選択肢、回答および自由記述。"""

    question: str
    options: list[_OfferedOption]
    answers: list[str]
    notes: str


def _question_answers_event(pairs: list[_AnsweredQuestion]) -> dict[str, Any] | None:
    """質問側の文面とユーザーが入力した値を由来別に分けたuserイベントへ変換する。

    選択肢のlabelと一致しない回答と、自由記述を伴う回答は従来の判断を是正した介入であるため、
    `answer_intervention`を付けて問題候補の母集団へ残す。Claude CodeとCodexの両方の回答をこの関数が処理するため、
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
        if block.get("name") not in {"AskUserQuestion", "ask_question"} or not isinstance(block.get("id"), str):
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
    choices = [{"label": choice} if isinstance(choice, str) else choice for choice in choices]
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


def _codex_question_call(
    payload: dict[str, Any],
) -> tuple[str, dict[str, tuple[str, list[_OfferedOption]]]] | None:
    """同期・非同期の質問定義から、回答が参照する識別子と質問文・選択肢を取得する。"""
    name = payload.get("name")
    if name not in {"request_user_input", "request_user_input_async"}:
        return None
    call_id = payload.get("call_id")
    arguments = _json_object(payload.get("arguments"))
    if not isinstance(call_id, str) or arguments is None:
        return None
    raw_questions = arguments.get("questions")
    if not isinstance(raw_questions, list):
        return None
    questions: dict[str, tuple[str, list[_OfferedOption]]] = {}
    for index, raw_question in enumerate(raw_questions):
        if not isinstance(raw_question, dict):
            continue
        asynchronous = name == "request_user_input_async"
        question_id = json.dumps([name, call_id, index]) if asynchronous else raw_question.get("id")
        question = raw_question.get("title" if asynchronous else "question")
        if isinstance(question_id, str) and isinstance(question, str):
            raw_options = raw_question.get("options")
            options = (
                [(option, "") for option in raw_options if isinstance(option, str)]
                if asynchronous and isinstance(raw_options, list)
                else _offered_options(raw_options)
            )
            questions.setdefault(question_id, (question, options))
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
    pending = pending_questions.get(call_id)
    output = _json_object(payload.get("output"))
    if pending is None or output is None:
        return None
    question_line, questions = pending
    raw_answers = output.get("answers")
    if not isinstance(raw_answers, dict):
        return None
    pending_questions.pop(call_id)
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


def _codex_async_reply_events(text: str, line: int, pending_questions: _CodexPendingQuestions) -> list[dict[str, Any]] | None:
    """後続の回答を呼出しIDと質問番号へ結び付け、対応しない回答は原文として残す。"""
    opening = "<send_user_message_question_reply>"
    closing = "</send_user_message_question_reply>"
    stripped = text.strip()
    if not stripped.startswith(opening) or not stripped.endswith(closing):
        return None
    try:
        replies = json.loads(stripped[len(opening) : -len(closing)])
    except json.JSONDecodeError, ValueError:
        return None
    if not isinstance(replies, list) or not replies:
        return None
    events: list[dict[str, Any]] = []
    unmatched: list[Any] = []
    for reply in replies:
        item_id = reply.get("questionItemId") if isinstance(reply, dict) else None
        answer = reply.get("answer") if isinstance(reply, dict) else None
        try:
            identity = json.loads(item_id) if isinstance(item_id, str) else None
        except json.JSONDecodeError, ValueError:
            identity = None
        if (
            not isinstance(identity, list)
            or len(identity) != 3
            or identity[0] != "request_user_input_async"
            or not isinstance(identity[1], str)
            or not isinstance(identity[2], int)
            or isinstance(identity[2], bool)
            or not isinstance(answer, str)
        ):
            unmatched.append(reply)
            continue
        pending = pending_questions.get(identity[1])
        key = json.dumps(identity)
        definition = pending[1].get(key) if pending else None
        if pending is None or definition is None:
            unmatched.append(reply)
            continue
        question, options = definition
        event = _question_answers_event([_AnsweredQuestion(question, options, [answer], "")])
        if event is None:
            unmatched.append(reply)
            continue
        if not options:
            # 非同期APIでoptionsを省いた質問は自由記述であり、候補から選択回答として除かない。
            event["answer_intervention"] = True
        event.update(
            line=line,
            question_line=pending[0],
            answer_line=line,
            question_call_id=identity[1],
            question_index=identity[2],
        )
        events.append(event)
        del pending[1][key]
        if not pending[1]:
            del pending_questions[identity[1]]
    if not events:
        return None
    if unmatched:
        event = _event("user", opening + "\n" + json.dumps(unmatched, ensure_ascii=False) + "\n" + closing)
        if event is not None:
            events.append(event)
    return events
