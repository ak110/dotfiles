# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""PreToolUse統合フックのうち、Bashコマンドを対象とする遮断検査。"""

from __future__ import annotations

import pathlib
import re
import shlex
import sys
from collections.abc import (
    Iterable,
    Sequence,
)
from typing import TYPE_CHECKING


from agent_toolkit._hooks.bash_command_parser import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    _GLOBAL_OPTIONS_WITH_VALUE,
    _GLOBAL_OPTIONS_WITHOUT_VALUE,
    split_bash_segments,
)


if TYPE_CHECKING:
    from agent_toolkit._hooks.pretooluse.dispatch import (
        _ExecutionSegment,
        _extract_execution_segments,
    )
    from agent_toolkit._hooks.pretooluse.notices import (
        _block_notice,
    )


# --- Bash: パターン一致によるプロセス終了の検出 ---

_GIT_GREP_VALUED_OPTIONS = frozenset(
    {"-A", "-B", "-C", "-m", "--after-context", "--before-context", "--context", "--max-count"}
)


def _attached_short_value_option(token: str, valued: Iterable[str]) -> str | None:
    """値を密着させた短縮オプションの形であれば、当該オプション名を返す。

    `-A14`のように値を空白なしで連結した形は対象コマンドが受理する1つのトークンである。
    短縮オプションの連結として1文字ずつ照合すると、値の各文字が受理集合に無いという判定になる。
    """
    if not token.startswith("-") or token.startswith("--"):
        return None
    return next(
        (
            option
            for option in valued
            if option.startswith("-") and not option.startswith("--") and token.startswith(option) and len(token) > len(option)
        ),
        None,
    )


_PROCESS_KILL_BY_PATTERN_RE = re.compile(r"(?<![\w-])(pkill|killall)(?![\w-])")
_PROCESS_KILL_UNSAFE_MARKERS = frozenset("$`(){}")
_PROCESS_KILL_LITERAL_SEARCH_COMMANDS = frozenset({"egrep", "fgrep", "grep", "rg"})


def _git_grep_literal_pattern_indices(arguments: Sequence[str]) -> set[int]:
    """`git grep`がリテラル検索パターンとして読む引数位置を返す。"""
    indices: set[int] = set()
    pattern_seen = False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        option_name = token.split("=", 1)[0]
        if option_name in _GIT_GREP_PATTERN_OPTIONS:
            pattern_seen = True
            if "=" in token:
                indices.add(index)
            elif index + 1 < len(arguments):
                index += 1
                indices.add(index)
        elif _attached_short_value_option(token, _GIT_GREP_PATTERN_OPTIONS) is not None:
            pattern_seen = True
            indices.add(index)
        elif option_name in _GIT_GREP_PATTERN_FILE_OPTIONS:
            pattern_seen = True
            if "=" not in token:
                index += 1
        elif _attached_short_value_option(token, _GIT_GREP_PATTERN_FILE_OPTIONS) is not None:
            pattern_seen = True
        elif option_name in _GIT_GREP_VALUED_OPTIONS:
            if "=" not in token:
                index += 1
        elif not token.startswith("-") and not pattern_seen:
            pattern_seen = True
            indices.add(index)
        index += 1
    return indices


def _git_log_literal_search_indices(arguments: Sequence[str]) -> set[int]:
    """`git log`が検索語として読む引数位置を返す。"""
    indices: set[int] = set()
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        if token in {"-S", "-G", "--grep"} and index + 1 < len(arguments):
            index += 1
            indices.add(index)
        elif (token.startswith(("-S", "-G")) and len(token) > 2) or token.startswith("--grep="):
            indices.add(index)
        index += 1
    return indices


def _has_active_process_kill_syntax(segment: str) -> bool:
    """区間に引用で無効化されていないシェル構文があれば真を返す。"""
    quote: str | None = None
    escaped = False
    for character in segment:
        if escaped:
            escaped = False
            continue
        if quote != "'" and character == "\\":
            escaped = True
            continue
        if quote == "'":
            if character == "'":
                quote = None
            continue
        if quote == '"':
            if character == '"':
                quote = None
            elif character in "$`":
                return True
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in _PROCESS_KILL_UNSAFE_MARKERS or character in "\n\r":
            return True
    return False


def _has_unsafe_process_kill_match(segment: str) -> bool:
    """禁止語を含む区間が、既知の検索コマンドのリテラル引数でなければ真を返す。"""
    try:
        raw_tokens = shlex.split(segment, posix=True)
    except ValueError:
        return True
    if not raw_tokens:
        return False
    command_name = pathlib.PurePosixPath(raw_tokens[0]).name
    attached_pager_match = command_name == "git" and any(
        token.startswith("-O") and _PROCESS_KILL_BY_PATTERN_RE.search(token[2:]) for token in raw_tokens
    )
    if not attached_pager_match and not any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in raw_tokens):
        return False
    if _has_active_process_kill_syntax(segment):
        return True
    if command_name == "git":
        parsed_segments = _extract_execution_segments(segment)
        if len(parsed_segments) != 1 or tuple(raw_tokens) != parsed_segments[0].tokens:
            return True
        subcommand = _git_subcommand_tokens(parsed_segments[0])
        if subcommand is None or subcommand[0] not in {"grep", "log"}:
            return True
        name, arguments = subcommand
        prefix = raw_tokens[: len(raw_tokens) - len(arguments)]
        if any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in prefix):
            return True
        safe_indices = (
            _git_grep_literal_pattern_indices(arguments) if name == "grep" else _git_log_literal_search_indices(arguments)
        )
        return any(
            (
                _PROCESS_KILL_BY_PATTERN_RE.search(token)
                or name == "grep"
                and token.startswith("-O")
                and _PROCESS_KILL_BY_PATTERN_RE.search(token[2:])
            )
            and index not in safe_indices
            for index, token in enumerate(arguments)
        )
    if command_name not in _PROCESS_KILL_LITERAL_SEARCH_COMMANDS:
        return True
    return not any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in raw_tokens[1:])


def _check_bash_process_kill_by_pattern(command: str) -> bool:
    """`pkill`・`killall`等パターン指定によるプロセス終了をブロックする。

    対象の所有権を確認できないパターン一致の一括終了は事故の危険があるため禁止する。
    自身が起動して識別子（PID）を確認したプロセスに対する`kill <PID>`形式は対象外とする。
    ヒアドキュメント本文をマスクした文字列を解析し、禁止語が実行位置ではなく、安全な引数位置の
    リテラルだと確定できる区間だけを許可する。
    """
    matching_segments = [segment for segment in split_bash_segments(command) if "pkill" in segment or "killall" in segment]
    if not matching_segments or not any(_has_unsafe_process_kill_match(segment) for segment in matching_segments):
        return False
    print(
        _block_notice(
            "blocked: パターン一致によるプロセス終了（`pkill`／`killall`）は、対象プロセスの所有を確認できないため禁止する。",
            fix="自身が起動しPIDで特定したプロセスに対して`kill <PID>`を使う。",
        ),
        file=sys.stderr,
    )
    return True


def _git_subcommand_tokens(segment: _ExecutionSegment) -> tuple[str, tuple[str, ...]] | None:
    """`git`区間のサブコマンド名と、当該サブコマンド以降の引数を返す。"""
    if not segment.resolved or not segment.tokens:
        return None
    if pathlib.PurePath(segment.tokens[0]).name != "git":
        return None
    index = 1
    tokens = segment.tokens
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            return token, tuple(tokens[index + 1 :])
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token in _GLOBAL_OPTIONS_WITHOUT_VALUE or "=" in token:
            index += 1
            continue
        return None
    return None


_GIT_GREP_PATTERN_OPTIONS: frozenset[str] = frozenset({"-e", "--regexp"})
_GIT_GREP_PATTERN_FILE_OPTIONS: frozenset[str] = frozenset({"-f", "--file"})
