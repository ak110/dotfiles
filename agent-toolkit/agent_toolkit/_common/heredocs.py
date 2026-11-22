from __future__ import annotations

import dataclasses
import functools

from agent_toolkit._common.shell_quoting import QuotingScanner


@dataclasses.dataclass(frozen=True)
class HeredocDeclaration:
    delimiter: str
    strip_tabs: bool
    expands: bool


@dataclasses.dataclass(frozen=True)
class HeredocBody:
    start: int
    end: int
    terminator_end: int
    expands: bool


_HEREDOC_DELIMITER_END = frozenset(" \t\r\n;|&<>()")


_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


def _heredoc_delimiter(line: str, start: int) -> tuple[str, bool, int] | None:
    pieces: list[str] = []
    quoted = False
    index = start
    while index < len(line) and line[index] not in _HEREDOC_DELIMITER_END:
        char = line[index]
        if char == "\\":
            if index + 1 >= len(line):
                return None
            pieces.append(line[index + 1])
            quoted = True
            index += 2
        elif char == "'":
            end = line.find("'", index + 1)
            if end < 0:
                return None
            pieces.append(line[index + 1 : end])
            quoted = True
            index = end + 1
        elif char == '"':
            index += 1
            while index < len(line) and line[index] != '"':
                if line[index] == "\\" and index + 1 < len(line) and line[index + 1] in _DOUBLE_QUOTE_ESCAPABLE:
                    index += 1
                pieces.append(line[index])
                index += 1
            if index >= len(line):
                return None
            quoted = True
            index += 1
        else:
            pieces.append(char)
            index += 1
    return "".join(pieces), quoted, index


def _heredoc_declarations(line: str) -> list[HeredocDeclaration]:
    declarations: list[HeredocDeclaration] = []
    scanner = QuotingScanner(line)
    arithmetic_depth = 0
    word_boundary = True
    while scanner.index < len(line):
        was_escaped = scanner.escaped
        if scanner.consume_quoted():
            if was_escaped:
                word_boundary = False
            continue
        index = scanner.index
        char = line[index]
        if arithmetic_depth:
            if char == "(":
                arithmetic_depth += 1
            elif char == ")":
                arithmetic_depth -= 1
            scanner.index += 1
            continue
        if char in {"'", '"'}:
            word_boundary = False
            scanner.enter_quote(char)
            continue
        if line.startswith("$((", index):
            arithmetic_depth = 2
            word_boundary = False
            scanner.index += 3
            continue
        if line.startswith("((", index):
            arithmetic_depth = 2
            word_boundary = False
            scanner.index += 2
            continue
        if char == "#" and word_boundary:
            break
        if not line.startswith("<<", index) or line.startswith("<<<", index):
            word_boundary = char in " \t;&|()"
            scanner.index += 1
            continue

        cursor = index + 2
        strip_tabs = cursor < len(line) and line[cursor] == "-"
        if strip_tabs:
            cursor += 1
        while cursor < len(line) and line[cursor] in {" ", "\t"}:
            cursor += 1
        parsed = _heredoc_delimiter(line, cursor)
        if parsed is None:
            break
        delimiter, quoted, cursor = parsed
        if delimiter:
            declarations.append(HeredocDeclaration(delimiter, strip_tabs, expands=not quoted))
        word_boundary = False
        scanner.index = cursor
    return declarations


def heredoc_bodies(command: str) -> list[HeredocBody]:
    return list(_cached_heredoc_bodies(command))


@functools.lru_cache(maxsize=256)
def _cached_heredoc_bodies(command: str) -> tuple[HeredocBody, ...]:
    bodies: list[HeredocBody] = []
    line_start = 0
    while line_start < len(command):
        line_end = command.find("\n", line_start)
        if line_end < 0:
            line_end = len(command)
        declarations = _heredoc_declarations(command[line_start:line_end])
        cursor = line_end + (line_end < len(command))
        for declaration in declarations:
            body_start = cursor
            body_end = terminator_end = len(command)
            while cursor < len(command):
                body_line_end = command.find("\n", cursor)
                if body_line_end < 0:
                    body_line_end = len(command)
                content_end = body_line_end - (body_line_end > cursor and command[body_line_end - 1] == "\r")
                content = command[cursor:content_end]
                candidate = content.lstrip("\t") if declaration.strip_tabs else content
                line_head = cursor
                cursor = body_line_end + (body_line_end < len(command))
                if candidate == declaration.delimiter:
                    body_end, terminator_end = line_head, body_line_end
                    break
            bodies.append(HeredocBody(body_start, body_end, terminator_end, declaration.expands))
        line_start = cursor
    return tuple(bodies)


def _blank_line(masked: list[str], start: int, end: int, *, separator: bool = False) -> None:
    for index in range(start, end):
        if masked[index] not in {"\r", "\n"}:
            masked[index] = " "
    if separator and start < end:
        masked[start] = ";"


def mask_heredoc_bodies(command: str) -> str:
    masked = list(command)
    for body in heredoc_bodies(command):
        _blank_line(masked, body.start, body.end)
        _blank_line(masked, body.end, body.terminator_end, separator=True)
    return "".join(masked)
