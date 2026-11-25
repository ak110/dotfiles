import re

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import markdown_headings as _markdown_headings

ANSWER_MARKER = "<!-- ユーザーはこの行以降に回答を追記する -->"
"""UWIエントリの回答欄開始位置を示すHTMLコメント。  pyfltr: allow-comment

`add.add_entries`が投入時に付与し、`answer_uwi`・`cmd_answer`が回答本文の切り出しに使う。
本文字列を直接記述せず、常に本定数を参照する。
"""

QUESTION_HEADING = "## 質問"

ANSWER_HEADING = "## 回答"

_RESERVED_MARKUP_HEADINGS = (QUESTION_HEADING, ANSWER_HEADING)


_SENTENCE_BOUNDARY = re.compile(r"(?<=[。．.!！])|\n")
_TRAILING_QUESTION = re.compile(r"か[。．]?(?:[（(][^（）()]*[）)])?[」』）)]*[。．]?$")


def _looks_like_question(message: str) -> bool:
    """UWIの質問本文の冒頭に問い（疑問文）があるかを簡易判定する。  pyfltr: allow-comment

    判定条件の定義元は`agent-toolkit:atk-mode/references/uwi-format.md`「構成」であり、
    同節は冒頭に疑問文を置き、末尾を`## 判断材料`とすることを質問の種類によらず定める。
    このため最初の行頭`## `より前（見出しが無ければ全体）だけを判定範囲とし、範囲内に`？`・`?`を含む文、
    または「か」で終わる文（後ろに句点、閉じ括弧、括弧書きの補足が続く場合を含む）が1つ以上あれば問いとみなす。
    規定の位置や対象の種類を変える改訂では、本関数と`warn_question_quality`も同じ変更単位で直す。
    高度な自然言語処理は導入せず、誤検知は許容してユーザーが目視で気づける警告にとどめる。
    """
    lead = re.split(r"^## ", message, maxsplit=1, flags=re.MULTILINE)[0]
    for sentence in _SENTENCE_BOUNDARY.split(lead):
        stripped = sentence.strip()
        if "？" in stripped or "?" in stripped or _TRAILING_QUESTION.search(stripped):
            return True
    return False


def _detect_self_containment_deficiency(message: str) -> str | None:

    identifier_pattern = re.compile(r"(?<![0-9A-Za-z])(?:fb|Q|FB)\s?\d{2,}(?![0-9A-Za-z])")
    context_words = (
        "のため",
        "という",
        "について",
        "背景",
        "経緯",
        "対象",
        "実装",
        "反映",
        "採否",
        "判定",
    )
    match = identifier_pattern.search(message)
    if match is not None:
        window_start = max(0, match.start() - 30)
        window_end = min(len(message), match.end() + 30)
        surrounding = message[window_start:window_end]
        if not any(word in surrounding for word in context_words):
            return "一時識別子の単独使用"
    return None


def strip_unanswered_uwi_wrapper(body: str) -> str:
    newline_without_crlf_split = r"(?:\r\n|\r(?!\n)|\n)"
    blank = rf"(?:{newline_without_crlf_split}[^\S\r\n]*)"
    leading = re.match(
        rf"\A(?:[^\S\r\n]*{newline_without_crlf_split})*{re.escape(QUESTION_HEADING)}(?:{newline_without_crlf_split}|\Z)"
        rf"(?:[^\S\r\n]*{newline_without_crlf_split})*",
        body,
    )
    if leading is not None:
        body = body[leading.end() :]
    trailing = re.search(
        rf"(?:\A|(?<=[\r\n])){re.escape(ANSWER_HEADING)}{blank}*"
        rf"(?:{newline_without_crlf_split}{re.escape(ANSWER_MARKER)}{blank}*)?\Z",
        body,
    )
    if trailing is not None:
        body = body[: trailing.start()].rstrip("\r\n")
    return body


def question_opening(body: str) -> str:
    body = strip_unanswered_uwi_wrapper(body)
    end = next(
        (token.map[0] for token, _name in _markdown_headings.parse_headings(body, 2) if token.level == 0 and token.map),
        None,
    )
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", body)
    start = next(
        (token.map[0] for token, _name in _markdown_headings.parse_headings(body, 1) if token.level == 0 and token.map),
        0,
    )
    if (end is not None and start >= end) or any(line.strip() for line in lines[:start]):
        start = 0
    return "".join(lines[start:end])


def reject_reserved_uwi_markup(body: str) -> None:
    violations: list[str] = []
    if ANSWER_MARKER in body:
        violations.append("回答欄マーカー")
    for line in body.splitlines():
        if line.strip() in _RESERVED_MARKUP_HEADINGS:
            violations.append(f"見出し（{line.strip()}）")
    if violations:
        raise WebInputError(
            f"UWI本文にツールが自動付与する要素が含まれています: {', '.join(violations)}",
            next_action="本文には質問内容のみを書いてください",
        )


def warn_question_quality(filename: str, message: str, question_type: str | None) -> None:
    del question_type  # noqa
    if not _looks_like_question(message):
        _outcome.report_warning(
            f"{filename}の質問本文の冒頭（最初のH2見出しより前）に問い（疑問文）が無い。",
            next_action=(
                "最初のH2見出しより前に、回答者が何に答えるべきか分かる疑問文を置く。"
                f"直す場合は`atk wi edit {filename}`で本文を置き換える"
            ),
        )
    reason = _detect_self_containment_deficiency(message)
    if reason is not None:
        _outcome.report_warning(
            f"{filename}の質問本文が単独で判断可能な情報を欠く可能性がある（{reason}）。",
            next_action=(
                "`agent-toolkit:atk-mode/references/uwi-format.md`「載せる情報」に沿って、本文だけで対象が分かる形に見直す。"
                f"直す場合は`atk wi edit {filename}`で本文を置き換える"
            ),
        )
