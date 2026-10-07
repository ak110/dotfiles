"""Bashコマンドのheredocの宣言と本文の位置の抽出、本文を空白へ置き換えたコマンド文字列の生成。"""

from __future__ import annotations

import dataclasses

from agent_toolkit._common.shell_quoting import QuotingScanner


@dataclasses.dataclass(frozen=True)
class HeredocDeclaration:
    r"""コマンド行にある1つのheredocの宣言。

    `delimiter`はbashと同じく区切り語から引用を除いた語であり、終端行との比較に使う。
    `expands`は区切り語のどこにも引用（`'`・`"`・`\\`）が無く、bashが本文を展開することを表す。
    """

    delimiter: str
    strip_tabs: bool
    expands: bool


@dataclasses.dataclass(frozen=True)
class HeredocBody:
    """コマンド文字列上のheredoc本文と終端行の範囲。

    本文は`[start, end)`、終端行の内容（改行を除く）は`[end, terminator_end)`とする。
    終端行が無いまま末尾へ達した本文では`end`と`terminator_end`が文字列の長さに一致する。
    """

    start: int
    end: int
    terminator_end: int
    expands: bool


_HEREDOC_DELIMITER_END = frozenset(" \t\r\n;|&<>()")


_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


def _heredoc_delimiter(line: str, start: int) -> tuple[str, bool, int] | None:
    """区切り語を読み、引用を除いた語、引用の有無および語の直後の位置を返す。

    bashは区切り語へquote removalだけを適用し、語のどこかに引用があれば本文を展開しない。
    引用が閉じない場合はNoneを返す。
    """
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
    """コマンド行にあるheredocの宣言を出現順に返す。"""
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
    """コマンド文字列にあるheredocの本文と終端行の範囲を出現順に返す。

    本文の範囲と展開の有無は`_heredoc_declarations`の1つの定義から得る。
    本文のマスクと、展開される本文の置換の判定はいずれも本関数の結果を使う。
    """
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
    return bodies


def _blank_line(masked: list[str], start: int, end: int, *, separator: bool = False) -> None:
    """改行を保ち、指定範囲の行内容を空白または区切り標識へ置換する。"""
    for index in range(start, end):
        if masked[index] not in {"\r", "\n"}:
            masked[index] = " "
    if separator and start < end:
        masked[start] = ";"


def mask_heredoc_bodies(command: str) -> str:
    """heredoc本文を同じ長さの空白へ置換し、本文外の位置と改行数を保つ。

    終端行は区切り標識`;`へ置換し、heredoc以降のコマンドを別の区間として残す。
    """
    masked = list(command)
    for body in heredoc_bodies(command):
        _blank_line(masked, body.start, body.end)
        _blank_line(masked, body.end, body.terminator_end, separator=True)
    return "".join(masked)
