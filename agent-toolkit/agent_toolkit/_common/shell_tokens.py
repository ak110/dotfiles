from __future__ import annotations

import pathlib
import re
import shlex
from collections.abc import Sequence

_REDIRECTION_OPERATOR_PATTERN = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|>|<<<|<<-|<<|<>|<&|<)")
"""リダイレクト演算子で始まるトークンの先頭部分。

先頭のファイル記述子番号（`2>`）と、標準出力・標準エラーの同時指定（`&>`）を含む。
"""

_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


class ShellWord(str):
    quoted: bool
    expansion: bool
    redirection: str | None
    unquoted: str
    raw: str
    start: int
    end: int

    def __new__(
        cls,
        value: str,
        *,
        quoted: bool = False,
        expansion: bool = False,
        redirection: str | None = None,
        unquoted: str | None = None,
        raw: str | None = None,
        start: int = -1,
        end: int = -1,
    ) -> ShellWord:  # noqa: PYI034
        word = super().__new__(cls, value)
        word.quoted = quoted
        word.expansion = expansion
        word.redirection = redirection
        word.unquoted = value if unquoted is None else unquoted
        word.raw = value if raw is None else raw
        word.start = start
        word.end = end
        return word


def split_words(text: str) -> list[ShellWord]:
    words: list[ShellWord] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] in " \t\r\n":
            index += 1
            continue
        start = index
        value: list[str] = []
        unquoted: list[str] = []
        quoted = False
        expansion = False
        while index < length and text[index] not in " \t\r\n":
            char = text[index]
            if char == "\\":
                if index + 1 >= length:
                    raise ValueError("No escaped character")
                quoted = True
                value.append(text[index + 1])
                index += 2
                continue
            if char == "'":
                end = text.find("'", index + 1)
                if end < 0:
                    raise ValueError("No closing quotation")
                quoted = True
                value.append(text[index + 1 : end])
                index = end + 1
                continue
            if char == '"':
                quoted = True
                index += 1
                while True:
                    if index >= length:
                        raise ValueError("No closing quotation")
                    inner = text[index]
                    if inner == '"':
                        index += 1
                        break
                    if inner == "\\" and index + 1 < length and text[index + 1] in _DOUBLE_QUOTE_ESCAPABLE:
                        value.append(text[index + 1])
                        index += 2
                        continue
                    if inner in "$`":
                        expansion = True
                    value.append(inner)
                    index += 1
                continue
            if char in "$`":
                expansion = True
            value.append(char)
            unquoted.append(char)
            index += 1
        literal = "".join(unquoted)
        raw = text[start:index]
        redirection = None
        match = _REDIRECTION_OPERATOR_PATTERN.match(raw)
        if match is not None and raw[: match.end()] == literal[: match.end()]:
            redirection = "separate" if match.end() == len(raw) else "attached"
        words.append(
            ShellWord(
                "".join(value),
                quoted=quoted,
                expansion=expansion,
                redirection=redirection,
                unquoted=literal,
                raw=raw,
                start=start,
                end=index,
            )
        )
    return words


ATK_POSIX = "atk"
ATK_POWERSHELL = "atk.ps1"
ATK_BATCH = "atk.cmd"
ATK_LAUNCHER_NAMES = (ATK_POSIX, ATK_POWERSHELL, ATK_BATCH, "atk.py")
ATK_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


def is_atk_command(tokens: Sequence[str]) -> bool:
    return bool(tokens) and pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name in ATK_LAUNCHER_NAMES


def join_words(tokens: Sequence[str]) -> str:
    return " ".join(token.raw if isinstance(token, ShellWord) else shlex.quote(token) for token in tokens)


def redirection_kind(token: str) -> str | None:
    if isinstance(token, ShellWord):
        return token.redirection
    match = _REDIRECTION_OPERATOR_PATTERN.match(token)
    if match is None:
        return None
    return "separate" if match.end() == len(token) else "attached"


def strip_redirections(tokens: Sequence[str]) -> tuple[str, ...]:
    arguments: list[str] = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        kind = redirection_kind(token)
        if kind is None:
            arguments.append(token)
            continue
        skip_target = kind == "separate"
    return tuple(arguments)


def is_agents_wait_command(tokens: Sequence[str]) -> bool:
    return is_atk_command(tokens) and tuple(tokens[1:3]) == ("agents", "wait")


def is_agents_exit_session_command(tokens: Sequence[str]) -> bool:
    arguments = strip_redirections(tokens)
    if len(arguments) < 2 or not is_atk_command(arguments) or arguments[1] != "agents-exit-session":
        return False
    match arguments[2:]:
        case ():
            return True
        case ("--repo", path):
            return bool(path) and not path.startswith("-")
        case (option,):
            return option.startswith("--repo=") and bool(option.partition("=")[2])
        case _:
            return False
