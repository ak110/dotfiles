from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable

lazy from markdown_it import MarkdownIt
lazy from markdown_it.token import Token

from agent_toolkit._common import response_language_check as _response_language_check
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks import agent_id as _agent_id
from agent_toolkit._hooks import bugfix_gate, norm_excuse_gate, work_contract
from agent_toolkit._hooks import notice as _notice
from agent_toolkit._hooks.pretooluse.content_checks import _warn_mojibake
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.notices import _block_notice
from agent_toolkit._hooks.pretooluse.warning_context import format_warning_context
lazy from agent_toolkit._common.markdown_headings import without_literal_blocks

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
SOURCE_REVIEW_FILE = "external-source-review.md"
_SOURCE_REVIEW_WORK = re.compile(r"^\s*-?\s*作業[:：]\s*`?([^\s`]+)`?\s*$")
_SOURCE_URL_COLUMN = "資料URL"
_SOURCE_DEFINITION_COLUMN = "配布規範での定義元または非該当の理由"
_SOURCE_COMPARISON_COLUMN = "削る・まとめる・置き換える案"
_SOURCE_DECISION_COLUMN = "採否と理由"
_SOURCE_REVIEW_COLUMNS = (_SOURCE_URL_COLUMN, _SOURCE_DEFINITION_COLUMN, _SOURCE_COMPARISON_COLUMN, _SOURCE_DECISION_COLUMN)


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


def _external_source_urls(lines: Iterable[str]) -> set[str]:
    urls: set[str] = set()
    within_sources = False
    for line in lines:
        if heading := _MARKDOWN_HEADING.match(line):
            if len(heading[1]) <= 2:
                within_sources = heading[2].strip() in {"原本", "入力"}
            continue
        if not within_sources and not re.match(r"\s*-?\s*(?:外部資料|入力)[:：]", line):
            continue
        urls.update(work_contract.source_urls(line))
    return urls


def external_source_review_needed(session_id: str, tool_input: dict) -> str | None:
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
        recorded_urls = entry.get("urls")
        urls = {url for url in recorded_urls if isinstance(url, str)} if isinstance(recorded_urls, list) else set()
    elif any(
        (match := _SOURCE_REVIEW_WORK.fullmatch(line)) and match[1] in work_contract.EXTERNAL_SOURCE_WORKS for line in lines
    ):
        urls = set()
    else:
        return None
    urls.update(_external_source_urls(lines))
    if not urls:
        return None
    if contract is not None:
        try:
            table = contract.with_name(SOURCE_REVIEW_FILE).read_text(encoding="utf-8")
        except OSError, UnicodeError:
            pass
        else:
            return _external_source_table_error(table, urls)
    return f"`{SOURCE_REVIEW_FILE}`を読み取れません。"


def _external_source_cell_content(cell: str) -> str:
    inline = MarkdownIt("commonmark").parseInline(unicodedata.normalize("NFKC", cell))[0]
    text = "".join(token.content for token in inline.children or () if token.type in {"text", "code_inline"})
    return re.sub(r"[^\w]|_", "", text)


def _external_source_table_error(table: str, urls: set[str]) -> str | None:
    columns: dict[str, int] = {}
    covered: set[str] = set()
    for line_number, line in enumerate(without_literal_blocks(table).splitlines(), 1):
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            columns = {}
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line[1:-1])]
        if _SOURCE_URL_COLUMN in cells:
            for column in _SOURCE_REVIEW_COLUMNS:
                if cells.count(column) != 1:
                    return f"{line_number}行目の見出しに`{column}`を1列書いてください。"
            columns = {column: cells.index(column) for column in _SOURCE_REVIEW_COLUMNS}
            continue
        if not columns or columns[_SOURCE_URL_COLUMN] >= len(cells):
            continue
        url_cell = cells[columns[_SOURCE_URL_COLUMN]]
        url = next(work_contract.source_urls(url_cell), "")
        if url not in urls:
            continue
        for column in _SOURCE_REVIEW_COLUMNS[1:]:
            index = columns[column]
            cell = cells[index] if index < len(cells) else ""
            meaningful = _external_source_cell_content(cell)
            if not meaningful or (column == _SOURCE_COMPARISON_COLUMN and meaningful in {"なし", "無し"}):
                return f"{line_number}行目の`{column}`に記入がありません。"
        covered.add(url)
    if missing := urls - covered:
        return f"`{_SOURCE_URL_COLUMN}`に対応する行がありません: " + "、".join(sorted(missing))
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
