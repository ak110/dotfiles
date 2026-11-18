"""原因分析の記録（AWIの`## 原因分析`と計画のバグ調査の記録）の行頭語の解析と検査。

記録の形は`agent-basics:bugfix`の`SKILL.md`「記録」が定める。
バグ単位ごとに`期待:`・`原因:`・`処置:`・`広がり:`・`確認:`の行を置き、条件が成立した時だけ`再発:`・`再発防止:`の行を加える。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

from agent_toolkit._plan.structure.constants import (
    CAUSE_RECORD_OPTIONAL_LABELS,
    CAUSE_RECORD_REQUIRED_LABELS,
    PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX,
    PLAN_BUG_FILE_REFERENCE_PREFIX,
    PLAN_H2_BUG,
    PLAN_LEGACY_BUG_TABLE_HEADERS,
    PLAN_METADATA_BUG_FIELD,
)
from agent_toolkit._plan.structure.markdown import (
    PlanHeading,
    child_headings,
    extract_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)
from agent_toolkit._plan.structure.parsing import parse_plan_metadata
from agent_toolkit._plan.structure.sections_layout import _check_h1

_CAUSE_RECORD_LABELS: dict[str, str] = {**CAUSE_RECORD_REQUIRED_LABELS, **CAUSE_RECORD_OPTIONAL_LABELS}

# 長い語を先に置き、`再発防止:`を`再発`と読み違えないようにする。
_CAUSE_RECORD_LINE = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*)?(?P<label>"
    + "|".join(re.escape(label) for label in sorted(_CAUSE_RECORD_LABELS, key=len, reverse=True))
    + r")(?:\*\*)?\s*[:：](?:\*\*)?(?P<text>.*)$"
)

_EMPTY_TEXT_CHARS = " \t\u3000*_`~"


class CauseRecordLine(NamedTuple):
    """原因分析の記録の1行。`text`は行頭語と区切りの後ろの本文で、前後の空白と強調記号を除いた値である。"""

    lineno: int
    label: str
    text: str


def cause_record_lines(content: str) -> list[CauseRecordLine]:
    """本文から原因分析の記録の行（行頭語で始まる行）を出現順に返す。

    行の形は、任意のリスト記号（`-`・`*`・`+`）、任意の強調（`**`）、行頭語、`:`か`：`、本文の順とする。
    `**期待**:`と`**期待:**`のどちらの強調も受け付ける。
    コードフェンスとHTMLコメントの内側の行は、引用・例示として数えない。
    行番号は`content`の先頭を1とする。
    """
    return _match_record_lines(iter_markdown_body_lines(content))


def _match_record_lines(lines: Iterable[tuple[int, str]]) -> list[CauseRecordLine]:
    return [
        CauseRecordLine(lineno, match["label"], match["text"].strip(_EMPTY_TEXT_CHARS))
        for lineno, line in lines
        if (match := _CAUSE_RECORD_LINE.match(line)) is not None
    ]


class _CauseRecordUnit(NamedTuple):
    """バグ単位1件。`prefix`は診断の前に置く単位の所在で、記録全体が1単位の場合は空とする。"""

    prefix: str
    lines: list[tuple[int, str]]


def _cause_record_units(content: str) -> list[_CauseRecordUnit]:
    """記録をバグ単位へ分ける。

    H3が無ければ全体を1単位とする。
    H3があれば各H3の配下を1単位とし、最初のH3より前の本文は行頭語の行を含む場合だけ1単位とする。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    units = [
        _CauseRecordUnit(f"`### {heading.text}`: ", lines_within(body, *heading_subtree_range(headings, index)))
        for index, heading in enumerate(headings)
        if heading.level == 3
    ]
    if not units:
        return [_CauseRecordUnit("", body)]
    first_h3 = next(heading.lineno for heading in headings if heading.level == 3)
    preamble = [(lineno, line) for lineno, line in body if lineno < first_h3]
    if _match_record_lines(preamble):
        units.insert(0, _CauseRecordUnit("最初のH3より前: ", preamble))
    return units


def _is_legacy_unit(unit: _CauseRecordUnit, *, accept_short_record: bool) -> bool:
    """廃止した表を持つ単位か、表も行頭語の行も持たない旧来の短い記録である単位を旧形式と判定する。"""
    tables = extract_tables(unit.lines)
    if any(table.header in PLAN_LEGACY_BUG_TABLE_HEADERS for table in tables):
        return True
    if not accept_short_record or tables:
        return False
    return not _match_record_lines(unit.lines) and any(
        line.strip() and not line.lstrip().startswith("#") for _lineno, line in unit.lines
    )


def _label_line_errors(unit: _CauseRecordUnit) -> list[str]:
    """1単位の行頭語の不足と、本文が空の行を診断する。"""
    lines = _match_record_lines(unit.lines)
    present = {line.label for line in lines}
    errors = [
        f"`{label}:`の行が無い（書く内容: {content}）"
        for label, content in CAUSE_RECORD_REQUIRED_LABELS.items()
        if label not in present
    ]
    errors.extend(
        f"`{line.label}:`の行に本文が無い（同じ行に書く内容: {_CAUSE_RECORD_LABELS[line.label]}）"
        for line in lines
        if not line.text
    )
    return errors


def cause_record_errors(content: str, *, accept_legacy: bool = False) -> list[str]:
    """原因分析の記録がバグ単位ごとに必須の行頭語の行をそろえ、各行に本文を持つかを検査し、違反の一覧を返す。

    `content`はAWIの`## 原因分析`節の本文か、計画のバグ調査の記録（H1やH2の見出しを含んでよい）とする。
    H3が無ければ全体を1単位とし、H3があれば各H3の配下を1単位とする。
    最初のH3より前の本文は、行頭語の行を含む場合だけ1単位として検査する。
    H3がある場合の診断には、単位の所在を前置する。

    `accept_legacy`が偽（新規作成と、原因分析を変える編集）では、廃止した表で書いた単位を拒否する。
    真（保存済みの計画の読み取り）では、廃止した表を持つ単位と、表も行頭語の行も持たない旧来の短い記録を、
    表の構成や記入内容を問わず受理する。
    記入内容の妥当性は検収が判断し、ここでは行の有無と本文の有無だけを確かめる。
    """
    errors: list[str] = []
    for unit in _cause_record_units(content):
        if _is_legacy_unit(unit, accept_short_record=accept_legacy):
            if accept_legacy:
                continue
            errors.append(f"{unit.prefix}原因分析を廃止した表で書いている。表を除き、行頭語で始まる行で書く")
        errors.extend(unit.prefix + error for error in _label_line_errors(unit))
    return errors


def has_legacy_cause_record(content: str) -> bool:
    """原因分析の記録が、読み取りでだけ受理する旧形式のバグ単位を含む場合に真を返す。移行の案内にだけ用いる。"""
    return any(_is_legacy_unit(unit, accept_short_record=True) for unit in _cause_record_units(content))


def _bug_file_reference_values(section: list[tuple[int, str]]) -> list[str]:
    """バグ調査節直下の計画ファイル（バグ）参照行からパス文字列を抽出する。"""
    prefixes = (PLAN_BUG_FILE_REFERENCE_PREFIX, PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX)
    return [
        stripped[len(prefix) :].strip()
        for _lineno, line in section
        if (stripped := line.strip())
        for prefix in prefixes
        if stripped.startswith(prefix)
    ]


def _check_bug_sections(body: list[tuple[int, str]], headings: list[PlanHeading], index: int) -> list[str]:
    """`## バグ調査結果`の分離先参照、または旧配置の本文内のバグ単位が基準を満たすか確かめる。"""
    start, end = heading_subtree_range(headings, index)
    section = lines_within(body, start, end)
    references = _bug_file_reference_values(section)
    nonempty_lines = [line.strip() for _lineno, line in section if line.strip()]

    if child_headings(headings, index, 3):
        errors = cause_record_errors("\n".join(line for _lineno, line in section), accept_legacy=True)
        if references:
            return [f"`## {PLAN_H2_BUG}`は分離先参照または本文内のバグ単位のどちらか一方にする", *errors]
        return errors

    if len(references) == 1 and references[0] and len(nonempty_lines) == 1:
        return []
    return [f"`## {PLAN_H2_BUG}`直下に`{PLAN_BUG_FILE_REFERENCE_PREFIX}`で始まる分離先参照1行、またはバグ単位のH3が1件以上必要"]


def extract_bug_file_reference(content: str) -> str | None:
    """計画メタ情報または旧バグ調査節が単独で参照する分離先パスを返す。"""
    metadata, _errors = parse_plan_metadata(content)
    if metadata is not None and PLAN_METADATA_BUG_FIELD in metadata.values:
        return metadata.values[PLAN_METADATA_BUG_FIELD]
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is None:
        return None
    start, end = heading_subtree_range(headings, bug_index)
    section = lines_within(body, start, end)
    if child_headings(headings, bug_index, 3):
        return None
    references = _bug_file_reference_values(section)
    nonempty_lines = [line.strip() for _lineno, line in section if line.strip()]
    if len(references) != 1 or not references[0] or len(nonempty_lines) != 1:
        return None
    return references[0]


def has_legacy_bug_table(content: str) -> bool:
    """計画本文の`## バグ調査結果`がバグ単位を本文内に置く旧配置で、基準を満たす場合に真を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is None:
        return False
    return bool(child_headings(headings, bug_index, 3)) and not _check_bug_sections(body, headings, bug_index)


def check_bug_file_structure(content: str) -> list[str]:
    """計画ファイル（バグ）のH1と見出しの階層、バグ単位ごとの原因分析の記録が基準を満たすか確かめる。

    保存済みの計画を読めるよう、廃止した表を持つバグ単位は`cause_record_errors`の読み取り互換で受理する。
    新規作成と改訂での旧形式の拒否は、`has_legacy_cause_record`による移行の案内が担う。
    """
    headings = extract_headings(content)
    errors = _check_h1(headings)
    if any(heading.level == 2 for heading in headings):
        errors.append("バグ調査ファイルにH2は置かない")
    if any(heading.level >= 4 for heading in headings):
        errors.append("バグ調査ファイルにH4以深の見出しは置かない")
    errors.extend(cause_record_errors(content, accept_legacy=True))
    return errors
