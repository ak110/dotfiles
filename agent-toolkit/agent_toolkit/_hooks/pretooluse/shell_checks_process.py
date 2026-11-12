"""プロセス終了コマンドの所有権保護と、自身に一致するプロセス検索の遮断。"""

from __future__ import annotations

import pathlib
import re
import warnings
lazy from collections.abc import Sequence

lazy from agent_toolkit._common.bash_invocations import BashInvocation, extract_bash_invocations
lazy from agent_toolkit._common.shell_segments import extract_execution_segments, split_bash_segments
lazy from agent_toolkit._common.shell_tokens import split_words
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks.pretooluse import large_reads
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._hooks.pretooluse.shell_checks_search import (
    _PROCESS_KILL_BY_PATTERN_RE,
    _PROCESS_KILL_LITERAL_SEARCH_COMMANDS,
    _PROCESS_KILL_UNSAFE_MARKERS,
    _git_grep_literal_pattern_indices,
    _git_log_literal_search_indices,
)


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


_ARGUMENT_EXECUTORS = frozenset(
    {
        "awk",
        "bash",
        "builtin",
        "busybox",
        "dash",
        "deno",
        "fish",
        "gawk",
        "git",
        "ksh",
        "lua",
        "mawk",
        "node",
        "perl",
        "php",
        "ruby",
        "sed",
        "sh",
        "zsh",
        "chroot",
        "command",
        "doas",
        "env",
        "eval",
        "exec",
        "find",
        "flock",
        "ionice",
        "nice",
        "nohup",
        "parallel",
        "script",
        "setsid",
        "ssh",
        "stdbuf",
        "strace",
        "su",
        "sudo",
        "time",
        "timeout",
        "unbuffer",
        "watch",
        "xargs",
    }
)


"""引数として受け取った語をプログラムとして実行し得るコマンド（ラッパー、シェル、インタプリタ、gitの設定値など）。

引数に終了コマンド名があれば実行位置と区別できない。gitは`-c`の設定値やpagerから実行するため含め、
検索語として安全な`git grep`・`git log`の位置は後段の判定が扱う。
"""


def _process_kill_word_only_in_data(invocations: Sequence[BashInvocation]) -> bool:
    """共通解析が確定した全ての実行位置で、終了コマンド名がデータ引数にだけ現れるかを返す。

    `pkill`・`killall`自体を実行する呼出し、引数からプログラムを実行するコマンドの引数に現れる場合、
    値の決まらない引数を持つ呼出し、解析できない入力（呼出しが空）は、データ引数と確定できないため偽を返す。
    読取・検索の解析（`large_reads.arguments_are_data`）が引数をデータと確定した呼出しは、実行名の形の語もデータとして扱う。
    """
    if not invocations:
        return False
    for invocation in invocations:
        tokens = invocation.segment.tokens
        if not invocation.arguments_known or not tokens:
            return False
        name = pathlib.PurePosixPath(tokens[0]).name
        if name in {"pkill", "killall"}:
            return False
        # 読取・検索の解析が引数をデータと確定した呼出し（`sed -n '1p'`・`awk '{print}'`の読取を含む）は、
        # 引数を実行し得るコマンド名でも引数を実行しない。
        data = large_reads.arguments_are_data(tokens)
        executor = name in _ARGUMENT_EXECUTORS or is_python_token(name)
        if not data and executor and any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in tokens[1:]):
            return False
        # 引数から実行するコマンドの集合は網羅できないため（`docker exec`、`npm exec`など）、読取・検索の解析が
        # データと確定した呼出しを除き、語が実行名の形で現れる引数（語そのもの、または後ろに空白が続く本文）は
        # データと確定しない。
        if not data and any(_process_kill_command_shaped(token) for token in tokens[1:]):
            return False
    return True


def _process_kill_command_shaped(token: str) -> bool:
    """引数の語が、別のコマンドに実行名として渡され得る形かを返す。

    語そのものか、本文の先頭・空白・区切り記号の直後に置かれて後ろに空白が続く場合を実行名の形とする。
    `/tmp/pkill dir/out.log`のようにパスの区切りの直後にある語はディレクトリ名として扱う。
    """
    if pathlib.PurePosixPath(token).name in {"pkill", "killall"}:
        return True
    return any(
        token[match.end() : match.end() + 1].isspace()
        and (match.start() == 0 or token[match.start() - 1].isspace() or token[match.start() - 1] in ";&|(`'\"")
        for match in _PROCESS_KILL_BY_PATTERN_RE.finditer(token)
    )


def _has_unsafe_process_kill_match(segment: str) -> bool:
    """共通解析の実行位置とデータ引数を分け、未確定の実行と既知の終了操作を保護する。"""
    try:
        raw_tokens = split_words(segment)
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
    if not attached_pager_match and _process_kill_word_only_in_data(extract_bash_invocations(segment)):
        return False
    if command_name == "git":
        parsed_segments = extract_execution_segments(segment)
        if len(parsed_segments) != 1 or tuple(raw_tokens) != parsed_segments[0].tokens:
            return True
        subcommand = git_subcommand_tokens(parsed_segments[0])
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
    # 検索語のリテラルは許可するが、外部プログラムを起動する検索の指定（`rg --pre`）は実行位置と区別できない。
    if command_name not in _PROCESS_KILL_LITERAL_SEARCH_COMMANDS or not large_reads.arguments_are_data(raw_tokens):
        return True
    return not any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in raw_tokens[1:])


def _check_bash_process_kill_by_pattern(command: str) -> str | None:
    """`pkill`・`killall`等パターン指定によるプロセス終了をブロックする。

    対象の所有権を確認できないパターン一致の一括終了は他者のプロセスを停止する危険があるため禁止する。
    自身が起動して識別子（PID）を確認したプロセスに対する`kill <PID>`形式は対象外とする。
    ヒアドキュメント本文をマスクした文字列を共通解析へ渡し、終了コマンド名がデータ引数にだけ現れる読取・表示・検索と、
    既知の検索用リテラルを許可する。語の存在だけでは終了操作と判定しない。
    実行位置を確定できない入力、引数を実行し得るコマンド、置換・グループを含む区間は保守的な判定を維持する。
    """
    matching_segments = [segment for segment in split_bash_segments(command) if "pkill" in segment or "killall" in segment]
    if not matching_segments or not any(_has_unsafe_process_kill_match(segment) for segment in matching_segments):
        return None
    return _block_notice(
        "blocked: パターン一致によるプロセス終了（`pkill`／`killall`）は、対象プロセスの所有を確認できないため禁止する。",
        fix=(
            "自身が起動しPIDで特定したプロセスに対して`kill <PID>`を使え。"
            "検索語として使う場合は`rg`・`grep`・`git grep`・`git log -S`の引数へリテラルで書くか、"
            "`p[k]ill`のように文字クラスで書け。"
        ),
    )


# `pgrep`の値を取るオプション。パターンの位置を求めるとき、オプションの直後の値をパターンと取り違えないために使う。
_PGREP_VALUE_OPTIONS = frozenset(
    {
        "-d",
        "-g",
        "-G",
        "-P",
        "-s",
        "-t",
        "-u",
        "-U",
        "-F",
        "-O",
        "--delimiter",
        "--pgroup",
        "--group",
        "--parent",
        "--session",
        "--terminal",
        "--euid",
        "--uid",
        "--pidfile",
        "--older",
        "--ns",
        "--nslist",
        "--signal",
        "-r",
        "--runstates",
        "--cgroup",
        "--env",
    }
)


def _pgrep_full_pattern(tokens: Sequence[str]) -> str | None:
    """`pgrep -f`のパターンを返す。`-f`が無いか、引数の形を確定できない場合はNoneを返す。

    `-x`（コマンド行全体との完全一致）と`-v`（不一致の列挙）は、自身を囲むシェルを結果に含めないためNoneを返す。
    """
    full = False
    pattern: str | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            pattern = tokens[index + 1] if index + 1 < len(tokens) else None
            break
        if token in {"-x", "--exact", "-v", "--inverse"}:
            return None
        if token in {"-f", "--full"}:
            full = True
        elif token in _PGREP_VALUE_OPTIONS:
            index += 1
        elif token.startswith("--") and "=" in token:
            pass
        elif token.startswith("-") and len(token) > 1:
            # 短いオプションの連結（`-af`など）は、値を取る文字を含まない場合だけ解釈する。
            letters = token[1:]
            if any(f"-{letter}" in _PGREP_VALUE_OPTIONS for letter in letters) or "x" in letters or "v" in letters:
                return None
            full = full or "f" in letters
        elif pattern is None:
            pattern = token
        index += 1
    return pattern if full else None


def _check_bash_self_matching_pgrep(command: str) -> str | None:
    """パターンが自身のコマンド行に一致する`pgrep -f`をブロックする。

    Bashツールのコマンドはラッパーのシェルが引数として受け取るため、`pgrep -f`はそのシェルのコマンド行にも一致する。
    パターンがコマンド本文に一致する場合は、自身を囲むシェルを必ず検出する。
    この結果で待機するループ（`until ! pgrep -f X`、`kill -0 $(pgrep -f X)`）は終わらず、
    一覧の結果も自身を含んで誤る（2026年11月12日、待機ループが自身に一致して最長38分残った事例を複数観測した）。
    パターンを`[p]ytest`のように文字クラスで書けば自身に一致しないため、本文に一致しない場合は許可する。
    """
    if "pgrep" not in command:
        return None
    for invocation in extract_bash_invocations(command):
        tokens = invocation.segment.tokens
        if not invocation.arguments_known or not tokens or pathlib.PurePosixPath(tokens[0]).name != "pgrep":
            continue
        pattern = _pgrep_full_pattern(tokens)
        # pgrepのEREにあるPOSIXの文字クラス（`[[:alpha:]]`）はPythonの正規表現と解釈が異なる。
        # 単一引用符はラッパーの引用で別の文字列へ置き換わり、本文に一致しても実際のコマンド行に一致しない。
        # どちらも自己一致を確定できないため判定しない。
        if pattern is None or "[:" in pattern or "'" in pattern:
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                matches_itself = re.search(pattern, command) is not None
        except re.error:
            continue
        if matches_itself:
            return _block_notice(
                "blocked: `pgrep -f`のパターンがこのコマンド自身の本文に一致するため、"
                "コマンドを実行するシェル自身を検出し、待機や判定が誤る。",
                fix=(
                    "自身が起動したプロセスは起動時のPID（`$!`）か、ホストの背景実行・`atk agents wait`の識別子で待て。"
                    "プロセスの一覧が必要なら、`[p]ytest`のように先頭の1文字を文字クラスで書き、パターンが本文に一致しない形にせよ。"
                ),
            )
    return None
