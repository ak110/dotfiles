"""検索パターンとオプション終端の解析。"""

from __future__ import annotations

import pathlib
import re
lazy from collections.abc import Iterable, Sequence

lazy from agent_toolkit._common.bash_invocations import extract_bash_invocations
lazy from agent_toolkit._common.shell_tokens import strip_redirections
lazy from agent_toolkit._hooks.notice import _WARN_TAG
lazy from agent_toolkit._hooks.pretooluse.notices import _llm_notice
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import (
    _GIT_GREP_PATTERN_FILE_OPTIONS,
    _GIT_GREP_PATTERN_OPTIONS,
    git_subcommand_tokens,
)

_GIT_GREP_VALUED_OPTIONS = frozenset(
    {"-A", "-B", "-C", "-m", "--after-context", "--before-context", "--context", "--max-count", "--max-depth", "--threads"}
)


"""`git grep -h`の必須値付きオプション（git 2.43.0の確認。パターン用は別集合）。"""


_PROCESS_KILL_BY_PATTERN_RE = re.compile(r"(?<![\w-])(pkill|killall)(?![\w-])")


_PROCESS_KILL_UNSAFE_MARKERS = frozenset("$`(){}")


_PROCESS_KILL_LITERAL_SEARCH_COMMANDS = frozenset({"egrep", "fgrep", "grep", "rg"})


_GIT_GREP_SHORT_FLAGS = frozenset("EFGPivwahnHIlLczonqrsuVpWN")


def _bundled_search_value_option(token: str, *, command: str) -> tuple[str, str] | None:
    """既知の短形式だけの束から最初の値付きオプションと密着値を返す。

    未知の文字やpager指定の後ろを検索語と認定すると、プロセス終了保護の免除を広げるため走査を止める。
    """
    if not token.startswith("-") or token.startswith("--"):
        return None
    valued = "efABCm" if command == "git" else "efmABCdgtMTrjE" if command == "rg" else "efmABCDd"
    flags = _GIT_GREP_SHORT_FLAGS if command == "git" else frozenset("EFGPivwxabhnHIlLczonqrsRsuUZV")
    for position, char in enumerate(token[1:], 1):
        if char in valued:
            return "-" + char, token[position + 1 :]
        if char not in flags:
            return None
    return None


def _git_grep_argument_layout(arguments: Sequence[str]) -> tuple[set[int], int | None, int | None]:
    """検索パターンの位置、revision・パスの区切り、実際のオプション終端の位置を返す。

    gitの受理形式（git 2.47.3の実行で確認）は、最初の`--`より前に`-e`・`-f`か位置引数の検索パターンがあるかで分かれる。

    - ある場合: 最初の`--`がrevision・パスの区切りであり、その後ろは全てrevisionとパスとして読まれる
    - 無い場合: 最初の`--`はオプションの終端であり、直後の1トークンが検索パターンになる。
      その後ろに現れる最初の`--`がrevision・パスの区切りになる

    パターンの位置には`-e`の値と位置引数の検索パターンを含め、`-f`が読むファイル名は含めない。
    束ねた短形式は最初の値付きオプションで値の消費を止める。
    """
    indices: set[int] = set()
    pattern_seen = False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        short = _bundled_search_value_option(token, command="git")
        if short is not None:
            option, attached = short
            pattern_seen |= option in {"-e", "-f"}
            if not attached:
                index += 1
            if option == "-e" and index < len(arguments):
                indices.add(index)
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--") and not set(token[1:]) <= _GIT_GREP_SHORT_FLAGS:
            return indices, None, None
        option_name = token.split("=", 1)[0]
        if option_name in _GIT_GREP_PATTERN_OPTIONS:
            pattern_seen = True
            if "=" in token:
                indices.add(index)
            elif index + 1 < len(arguments):
                index += 1
                indices.add(index)
        elif option_name in _GIT_GREP_PATTERN_FILE_OPTIONS:
            pattern_seen = True
            if "=" not in token:
                index += 1
        elif option_name in _GIT_GREP_VALUED_OPTIONS:
            if "=" not in token:
                index += 1
        elif not token.startswith("-") and not pattern_seen:
            pattern_seen = True
            indices.add(index)
        index += 1
    if index >= len(arguments):
        return indices, None, None
    if pattern_seen:
        return indices, index, index
    pattern_index = index + 1
    if pattern_index >= len(arguments):
        return indices, None, index
    indices.add(pattern_index)
    separator = next((i for i in range(pattern_index + 1, len(arguments)) if arguments[i] == "--"), None)
    return indices, separator, index


def _git_grep_literal_pattern_indices(arguments: Sequence[str]) -> set[int]:
    """`git grep`がリテラル検索パターンとして読む引数位置を返す。"""
    return _git_grep_argument_layout(arguments)[0]


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


_OPTION_TERMINATOR_PATTERN_COMMANDS = frozenset({"rg", "grep", "egrep", "fgrep"})


"""`-e`・`-f`を`--`より前に置かない場合に、`--`の直後の1トークンを検索パターンとして受け取るコマンド。"""


_OPTION_TERMINATOR_PATTERN_OPTIONS = frozenset({"-e", "--regexp", "-f", "--file"})


"""`rg`・`grep`系で検索パターンを指定するオプション。`--`より前にあると`--`の後ろは全てパスとして読まれる。"""


_OPTION_TERMINATOR_GIT_SUBCOMMANDS = frozenset({"log", "diff", "show", "grep"})


"""`--`の後ろを全てパス指定として扱う`git`のサブコマンド。

例外として`git grep`は、`--`より前に検索パターンが無い場合に最初の`--`の直後を検索パターンとし、
続く`--`をrevision・パスの区切りとする（`_git_grep_argument_layout`）。
"""


def _options_after_terminator(arguments: Sequence[str], *, excluded: Iterable[int] = ()) -> list[str]:
    """引数列の最初の`--`より後ろにある、`-`で始まるトークンを返す。

    `excluded`の位置（検索パターンや区切りの`--`）は判定から除く。
    `-`単独は標準入力を表すデータであり、オプションに数えない。
    """
    if "--" not in arguments:
        return []
    skip = set(excluded)
    start = list(arguments).index("--") + 1
    return [
        token
        for index, token in enumerate(arguments)
        if index >= start and index not in skip and token.startswith("-") and token != "-"
    ]


def _pattern_command_slot(arguments: Sequence[str], *, command: str) -> set[int]:
    """`rg`・`grep`系で、終端後のパスとして警告しない引数位置を返す。

    `-e`・`--regexp`・`-f`・`--file`（`=`や`-eX`の連結形を含む）を`--`より前に置く場合は、
    `--`の後ろが全てパスとして読まれる（ripgrep 15.2.0とGNU grepの実行で確認）。
    実際の終端以前を除外し、明示パターンの無い入力では終端直後の検索語も除外する。
    """
    pattern_seen = False
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            # 値として消費した`--`を、後段の単純な終端検索で再分類しない。
            return set(range(index + 1 if pattern_seen else index + 2))
        option, separator, _value = token.partition("=")
        short = _bundled_search_value_option(token, command=command)
        if short is not None:
            pattern_seen |= short[0] in {"-e", "-f"}
            if not short[1]:
                index += 1
        elif option in _OPTION_TERMINATOR_PATTERN_OPTIONS:
            pattern_seen = True
            if not separator:
                index += 1
        index += 1
    return set(range(len(arguments)))


def _check_bash_option_after_terminator(command: str) -> str | None:
    """読取コマンドのオプション終端の後ろにあるオプションへ警告する。

    `--`の後ろは全てデータとして扱われるため、後ろへ置いた`--glob`などは`rg`・`grep`系・`git grep`では
    存在しないパスとして失敗し、`git log`・`git diff`・`git show`ではエラーを出力せずにパス指定として扱われ、誤った結果を返す。
    条文で配置を定めた後も同じ誤りが反復したため、実行の直前に判定する。
    読取入力を修正して再取得できるため警告し、通常の実行を続ける。
    外側に所属する既知の引数だけを判定し、置換内の語と展開結果が未確定の引数はオプションとして扱わない。
    `rg`・`grep`系と`git grep`では、`-e`・`-f`を`--`より前に置かない場合に`--`の直後を検索パターンとみなして除くため、
    `-`で始まるパターンは遮断しない。`git grep`ではその後ろのrevision・パスの区切り`--`も除く。
    下位コマンドへ`--`の後ろでオプションを渡すCLI（`uv run --`など）は対象コマンドに含めない。
    """
    for invocation in extract_bash_invocations(command):
        segment = invocation.segment
        if not segment.resolved or not segment.tokens:
            continue
        name = pathlib.PurePath(segment.tokens[0]).name
        if name in _OPTION_TERMINATOR_PATTERN_COMMANDS:
            arguments = list(strip_redirections(segment.tokens[1:]))
            found = _options_after_terminator(arguments, excluded=_pattern_command_slot(arguments, command=name))
            label = name
        else:
            subcommand = git_subcommand_tokens(segment)
            if subcommand is None or subcommand[0] not in _OPTION_TERMINATOR_GIT_SUBCOMMANDS:
                continue
            arguments = list(strip_redirections(subcommand[1]))
            excluded: set[int] = set()
            if subcommand[0] == "grep":
                pattern_indices, separator, terminator = _git_grep_argument_layout(arguments)
                excluded = pattern_indices | ({separator} if separator is not None else set())
                excluded.update(range(terminator + 1 if terminator is not None else len(arguments)))
            found = _options_after_terminator(arguments, excluded=excluded)
            label = f"git {subcommand[0]}"
        if not found:
            continue
        return _llm_notice(
            f"`{label}`のオプション終端`--`の後ろにオプション（{'、'.join(found)}）がある。"
            "`--`の後ろは全てデータとして扱われるため、これらはパスとして解釈され、"
            "存在しないパスとして失敗するか、エラーを出力せずに結果を限定する。",
            tag=_WARN_TAG,
            subject=command,
            removable_cause=True,
            fix=(
                "そのコマンド自身のオプションを`--`より前へ移し、`--`の後ろには検索パターンとパスだけを置いて再実行せよ。"
                "`-`で始まるパスを渡す場合は`./`を前置せよ。"
            ),
        )
    return None
