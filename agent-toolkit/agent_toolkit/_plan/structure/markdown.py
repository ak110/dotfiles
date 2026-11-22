from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from dataclasses import dataclass

import markdown_it
import markdown_it.common.html_re
import markdown_it.rules_inline
import markdown_it.token

from agent_toolkit._plan.structure.constants import h2_aliases

_EMPTY_CELL_FIX = "空のセルへ内容を記載し、列数を表頭へそろえる（検証コマンドの欄には実行するコマンドを書く）"


def _column_count_error(label: str, expected: int, row: tuple[str, ...], location: str) -> str:
    return (
        f"{label}の列数が一致しない（期待={expected}列、実際={len(row)}列）: {location}。"
        "セル内のリテラルな半角縦線は`\\|`へエスケープするか、半角縦線を使わない"
    )


def _check_fixed_table(
    lines: list[tuple[int, str]],
    header: tuple[str, ...],
    label: str,
    minimum_rows: int = 1,
) -> tuple[MarkdownTable | None, list[str]]:
    tables = extract_tables(lines)
    matching = [candidate for candidate in tables if candidate.header == header]
    if not matching:
        return None, [f"{label}は{list(header)}の列を持つ表にする"]
    table = matching[0]
    errors = [f"{label}の固定表は1件必要: 実際={len(matching)}件"] if len(matching) != 1 else []
    if len(table.rows) < minimum_rows:
        errors.append(f"{label}の表に1行以上の内容が必要")
    for index, row in enumerate(table.rows):
        if len(row) != len(header):
            errors.append(_column_count_error(f"{label}の表", len(header), row, table.row_location(index)))
        elif any(not cell for cell in row):
            errors.append(f"{label}の表に空cellがある: {table.row_location(index)}。{_EMPTY_CELL_FIX}")
    return table, errors


def _comma_separated_values(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(","))


_HEADING_PATTERN = re.compile(r"^(#{1,6}) +(.*?)\s*$")


def markdown_body_start_index(content: str) -> int:
    lines = content.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() in ("---", "..."):
            return index + 1
    return len(lines)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    while index > backslashes and text[index - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _code_span_comment_starts(content: str, inline_tokens: list[markdown_it.token.Token]) -> set[int]:
    parser = markdown_it.MarkdownIt("commonmark")
    spans: list[tuple[int, int]] = []

    def record_backtick(state: markdown_it.rules_inline.StateInline, silent: bool) -> bool:
        start = state.pos
        token_count = len(state.tokens)
        matched = markdown_it.rules_inline.backtick(state, silent)
        if matched and not silent and len(state.tokens) > token_count and state.tokens[-1].type == "code_inline":
            spans.append((start, state.pos))
        return matched

    parser.inline.ruler.at("backticks", record_backtick)

    def protected_markers(source: str) -> list[bool]:
        spans.clear()
        parser.parseInline(source)
        markers: list[bool] = []
        cursor = 0
        while (marker := source.find("<!--", cursor)) >= 0:
            markers.append(any(start <= marker < end for start, end in spans))
            cursor = marker + len("<!--")
        return markers

    lines = content.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    tokens_by_line_range: dict[tuple[int, int], list[markdown_it.token.Token]] = {}
    for token in inline_tokens:
        assert token.map is not None
        tokens_by_line_range.setdefault((token.map[0], token.map[1]), []).append(token)

    protected: set[int] = set()
    for (start_line, end_line), block_tokens in tokens_by_line_range.items():
        source_start = line_offsets[start_line]
        source_end = line_offsets[end_line]
        source = content[source_start:source_end]
        if len(block_tokens) == 1:
            marker_flags = protected_markers(source)
        else:
            marker_flags = []
            for token in block_tokens:
                marker_flags.extend(protected_markers(token.content))

        cursor = 0
        marker_index = 0
        while (marker := source.find("<!--", cursor)) >= 0:
            if marker_index >= len(marker_flags) or marker_flags[marker_index]:
                protected.add(source_start + marker)
            marker_index += 1
            cursor = marker + len("<!--")
    return protected


def _markdown_excluded_line_indices(content: str) -> set[int]:
    excluded: set[int] = set()
    inline_tokens: list[markdown_it.token.Token] = []
    parser = markdown_it.MarkdownIt("commonmark").enable("table")
    for token in parser.parse(content):
        if token.type == "fence" and token.map is not None:
            start, end = token.map
            excluded.update(range(start, end))
        elif token.type == "html_block" and token.map is not None and token.content.lstrip().startswith("<!--"):
            start, end = token.map
            if end - start > 1:
                excluded.update(range(start, end))
        elif token.type == "inline" and token.map is not None:
            inline_tokens.append(token)

    code_span_comment_starts = _code_span_comment_starts(content, inline_tokens)
    cursor = 0
    while (comment_start := content.find("<!--", cursor)) >= 0:
        start_line = content.count("\n", 0, comment_start)
        if start_line in excluded or _is_escaped(content, comment_start) or comment_start in code_span_comment_starts:
            cursor = comment_start + len("<!--")
            continue
        match = markdown_it.common.html_re.HTML_TAG_RE.match(content[comment_start:])
        if match is None or not match.group().startswith("<!--"):
            cursor = comment_start + len("<!--")
            continue
        comment_end = comment_start + len(match.group())
        end_line = content.count("\n", 0, comment_end - 1)
        if end_line > start_line:
            excluded.update(range(start_line, end_line + 1))
        cursor = comment_end
    return excluded


def iter_markdown_body_lines(content: str) -> Iterator[tuple[int, str]]:
    content = content.removeprefix("\ufeff")
    lines = content.splitlines()
    body_start = markdown_body_start_index(content)
    body = "\n".join(lines[body_start:])
    excluded = _markdown_excluded_line_indices(body)
    for body_index, line in enumerate(lines[body_start:]):
        if body_index not in excluded:
            yield body_start + body_index + 1, line


def markdown_body_text(content: str) -> str:
    return "\n".join(line for _lineno, line in iter_markdown_body_lines(content))


@dataclass(frozen=True)
class PlanHeading:
    lineno: int
    level: int
    text: str


def extract_headings(content: str) -> list[PlanHeading]:
    headings: list[PlanHeading] = []
    for lineno, line in iter_markdown_body_lines(content):
        match = _HEADING_PATTERN.match(line)
        if match is not None:
            headings.append(PlanHeading(lineno, len(match.group(1)), match.group(2)))
    return headings


def check_duplicate_headings(content: str) -> list[str]:
    """同じ祖先見出しの下に同じ文言の見出しが複数現れる状態を検出する。

    節の置換で撤回済みの記述が重複ブロックとして残ると、実装担当が旧案を確定文面として読む。
    判定キーへ祖先見出しの文言列を含めるのは、同じ文言の見出しが別の親の下に現れる構成が
    計画書式では正当なためである（既存計画531件を確認した結果、祖先見出しの文言列を含めた判定の一致は0件、
    文言だけの判定の一致は2ファイル）。
    """
    seen: dict[tuple[str, ...], int] = {}
    stack: list[PlanHeading] = []
    errors: list[str] = []
    for heading in extract_headings(content):
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        key = tuple(item.text for item in stack) + (heading.text,)
        first = seen.get(key)
        if first is None:
            seen[key] = heading.lineno
        else:
            parent = "/".join(key[:-1]) or "文書直下"
            errors.append(
                f"同じ見出しが重複している: `{'#' * heading.level} {heading.text}`"
                f"（{parent}配下、{first}行目と{heading.lineno}行目）。一方を削除するか、見出しの文言を区別する"
            )
        stack.append(heading)
    return errors


def heading_subtree_range(headings: list[PlanHeading], index: int) -> tuple[int, int | None]:
    level = headings[index].level
    for following in headings[index + 1 :]:
        if following.level <= level:
            return headings[index].lineno, following.lineno
    return headings[index].lineno, None


def lines_within(lines: list[tuple[int, str]], start: int, end: int | None) -> list[tuple[int, str]]:
    return [(lineno, line) for lineno, line in lines if lineno > start and (end is None or lineno < end)]


def child_headings(headings: list[PlanHeading], index: int, level: int) -> list[tuple[int, PlanHeading]]:
    start, end = heading_subtree_range(headings, index)
    return [
        (position, heading)
        for position, heading in enumerate(headings)
        if heading.level == level and heading.lineno > start and (end is None or heading.lineno < end)
    ]


def find_heading_index(headings: list[PlanHeading], level: int, text: str) -> int | None:
    accepted = h2_aliases(text) if level == 2 else (text,)
    return next(
        (index for index, heading in enumerate(headings) if heading.level == level and heading.text in accepted),
        None,
    )


@dataclass(frozen=True)
class MarkdownTable:
    lineno: int
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    row_linenos: tuple[int, ...]
    row_sources: tuple[str, ...]

    def row_labels(self) -> tuple[str, ...]:
        return tuple(row[0] if row else "" for row in self.rows)

    def row_location(self, index: int) -> str:
        if index < 0 or index >= len(self.rows):
            return ""
        return f"{self.row_linenos[index]}行目: {self.row_sources[index]}"


@functools.cache
def _table_parser() -> markdown_it.MarkdownIt:
    return markdown_it.MarkdownIt("commonmark").enable("table")


def _table_row_cells(line: str) -> tuple[str, ...]:
    parser = _table_parser()
    for count in range(1, line.count("|") + 2):
        delimiter = "|" + "|".join(["---"] * count) + "|"
        tokens = parser.parse(f"{line}\n{delimiter}\n")
        if not any(token.type == "table_open" for token in tokens):
            continue
        return tuple(token.content.strip() for token in tokens if token.type == "inline")
    return (line.strip(),)


def extract_tables(lines: list[tuple[int, str]]) -> list[MarkdownTable]:
    source_lines = [line for _lineno, line in lines]
    tables: list[MarkdownTable] = []
    lineno = 0
    header: tuple[str, ...] = ()
    rows: list[tuple[str, ...]] = []
    row_linenos: list[int] = []
    row_sources: list[str] = []
    in_body = False
    for token in _table_parser().parse("\n".join(source_lines)):
        if token.type == "table_open":
            assert token.map is not None
            lineno = lines[token.map[0]][0]
            header, rows, row_linenos, row_sources, in_body = (), [], [], [], False
        elif token.type == "thead_open":
            assert token.map is not None
            header = _table_row_cells(source_lines[token.map[0]])
        elif token.type == "tbody_open":
            in_body = True
        elif token.type == "tr_open" and in_body:
            assert token.map is not None
            source_index = token.map[0]
            rows.append(_table_row_cells(source_lines[source_index]))
            row_linenos.append(lines[source_index][0])
            row_sources.append(source_lines[source_index])
        elif token.type == "table_close":
            tables.append(MarkdownTable(lineno, header, tuple(rows), tuple(row_linenos), tuple(row_sources)))
    return tables
