"""プロセス終了コマンドの所有権保護と、自身に一致するプロセス検索の遮断。"""

from __future__ import annotations

import pathlib
import re
import warnings
lazy from collections.abc import Sequence

lazy from agent_toolkit._common.bash_invocations import _bash_substitution, extract_bash_invocations
lazy from agent_toolkit._common.shell_segments import extract_execution_segments, split_bash_segments
lazy from agent_toolkit._common.shell_tokens import split_words
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks.pretooluse import large_reads
lazy from agent_toolkit._hooks.pretooluse.notices import _block_notice
lazy from agent_toolkit._hooks.pretooluse.shell_checks_git import git_subcommand_tokens
lazy from agent_toolkit._hooks.pretooluse.shell_checks_search import (
    _PROCESS_KILL_BY_PATTERN_RE,
    _git_grep_literal_pattern_indices,
    _git_log_literal_search_indices,
)

# 区間の先頭にあって実行するコマンドの位置を後ろへずらす語（制御構文・グループ）。環境変数の代入も同様に除いて数える。
_SHELL_PREFIX_WORDS = frozenset({"(", "{", "!", "if", "then", "else", "elif", "while", "until", "do"})
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_]\w*=")


def _has_process_kill_substitution(segment: str) -> bool:
    """能動的なコマンド置換・プロセス置換の本文に終了コマンド名があるかを返す。

    置換の終端は共通解析で求め、外側のデータ引数にだけある語を置換内の語として扱わない。
    閉じない置換や改行を含む未確定の区間は保守的に真とする。
    通常の変数展開（`"$f"`）は実行位置を作らないため対象にしない。
    """
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(segment):
        character = segment[index]
        index += 1
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
        start = index - 1
        if character == "`" or segment.startswith("$(", start) or quote is None and segment.startswith(("<(", ">("), start):
            try:
                _, end = _bash_substitution(segment, start)
            except ValueError:
                return True
            if _PROCESS_KILL_BY_PATTERN_RE.search(segment[start:end]):
                return True
            index = end
            continue
        if quote == '"':
            if character == '"':
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in "\n\r":
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

# 引数が常にプログラムの起動指定になるコマンド（端末多重化、名前空間への入場、一時実行ツール）。
_STANDALONE_LAUNCHERS = frozenset({"bunx", "nsenter", "npx", "pipx", "screen", "tmux", "uvx"})

# サブコマンドや区切りで別のプログラムを起動するコマンドと、その起動を示す引数。
_SUBCOMMAND_LAUNCHERS = frozenset({"docker", "kubectl", "mise", "npm", "pnpm", "podman", "uv", "yarn"})
_LAUNCH_ARGUMENTS = frozenset({"--", "dlx", "exec", "run", "x"})


def _process_kill_command_shaped(token: str) -> bool:
    """引数の本文が、別のコマンドに実行名として渡され得る形かを返す。

    本文の先頭・空白・区切り記号の直後に置かれて後ろに空白が続く語を実行名の形とする（`'pkill -f x'`など）。
    `tmux send-keys`のような未知の起動手段へ渡る文字列を保護する。
    `/tmp/pkill dir/out.log`のようにパスの区切りの直後にある語はディレクトリ名として扱う。
    """
    return any(
        token[match.end() : match.end() + 1].isspace()
        and (match.start() == 0 or token[match.start() - 1].isspace() or token[match.start() - 1] in ";&|(`'\"")
        for match in _PROCESS_KILL_BY_PATTERN_RE.finditer(token)
    )


def _command_position_tokens(tokens: Sequence[str]) -> Sequence[str]:
    """環境変数の代入と制御構文の語を除き、実行するコマンドから始まる語を返す。"""
    index = 0
    while index < len(tokens) and (tokens[index] in _SHELL_PREFIX_WORDS or _ASSIGNMENT_RE.match(tokens[index])):
        index += 1
    return tokens[index:]


def _expands_command_name(segment: str) -> bool:
    """区間のコマンド名の位置が、変数展開やコマンド置換で決まるかを返す。"""
    try:
        tokens = _command_position_tokens(split_words(segment))
    except ValueError:
        return False
    return bool(tokens) and any(character in tokens[0] for character in "$`")


def _git_process_kill_match(segment: str, raw_tokens: Sequence[str]) -> bool:
    """`git`の区間で、語が検索語として安全な位置以外にあるかを返す。"""
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


def _runs_arguments(name: str, tokens: Sequence[str]) -> bool:
    """コマンドが、引数に渡された語をプログラムとして実行し得るかを返す。"""
    if name in _ARGUMENT_EXECUTORS or is_python_token(name):
        # 読取・検索の解析が引数をデータと確定した呼出し（`sed -n '1p'`・`awk '{print}'`の読取を含む）は引数を実行しない。
        return not large_reads.arguments_are_data(tokens)
    if name in _STANDALONE_LAUNCHERS:
        return True
    if name in _SUBCOMMAND_LAUNCHERS:
        return not _LAUNCH_ARGUMENTS.isdisjoint(tokens[1:])
    # 外部プログラムを起動する検索の指定（`rg --pre`）は実行位置と区別できない。
    return name == "rg" and any(token == "--pre" or token.startswith("--pre=") for token in tokens[1:])


def _has_unsafe_process_kill_match(segment: str) -> bool:
    """区間で、語が実行され得る位置にあるかを返す。

    コマンド名の位置、置換の中、実行名の形の本文、引数を実行し得るコマンドの引数にある語を実行位置とする。
    それ以外のコマンドの引数に語やその断片があるだけの区間は、検索・表示のデータとして扱う。
    """
    try:
        raw_tokens = split_words(segment)
    except ValueError:
        return True
    if not raw_tokens:
        return False
    if _has_process_kill_substitution(segment):
        return True
    tokens = _command_position_tokens(raw_tokens)
    if not tokens or _PROCESS_KILL_BY_PATTERN_RE.search(tokens[0]):
        return bool(tokens)
    name = pathlib.PurePosixPath(tokens[0]).name
    arguments = tokens[1:]
    if name == "git":
        return _git_process_kill_match(segment, raw_tokens)
    if any(_process_kill_command_shaped(token) for token in arguments):
        return True
    if not any(_PROCESS_KILL_BY_PATTERN_RE.search(token) for token in arguments):
        return False
    return _runs_arguments(name, tokens)


def _check_bash_process_kill_by_pattern(command: str) -> str | None:
    """`pkill`・`killall`等パターン指定によるプロセス終了をブロックする。

    対象の所有権を確認できないパターン一致の一括終了は他者のプロセスを停止する危険があるため禁止する。
    自身が起動して識別子（PID）を確認したプロセスに対する`kill <PID>`形式は対象外とする。
    判定は、語の出現ではなく、語が実行され得る位置にあるかで行う（`_has_unsafe_process_kill_match`）。
    形ごとの許可を足して誤遮断を直す方式は、列挙外の正常な形が必ず誤遮断になるため、同じ種類の誤遮断を繰り返した。
    許可リストへ戻さず、実行位置の列挙を直すこと。
    未知のラッパーへ語を独立した引数で渡す形は見逃すが、規範の保護が主であり、誤遮断を避けるため許容する。
    """
    segments = split_bash_segments(command)
    matching_segments = [segment for segment in segments if "pkill" in segment or "killall" in segment]
    if not matching_segments:
        return None
    # 語のある入力のどこかでコマンド名が変数展開で決まる場合は、展開の結果が終了コマンドかを確定できない。
    if not any(_has_unsafe_process_kill_match(segment) for segment in matching_segments) and not any(
        _expands_command_name(segment) for segment in segments
    ):
        return None
    return _block_notice(
        "blocked: パターン一致によるプロセス終了（`pkill`／`killall`）は、対象プロセスの所有を確認できないため禁止する。",
        fix=(
            "自身が起動しPIDで特定したプロセスに対して`kill <PID>`を使え。"
            "この遮断は、語がコマンド名、`sudo`・`xargs`・`sh -c`・`docker exec`などの起動する引数、"
            "コマンド置換、変数展開したコマンド名、実行名の形（`'pkill -f x'`）の本文にある場合に限る。"
            "検索・表示のコマンドの引数に語を書くだけなら遮断されないため、その位置へ移すか、`p[k]ill`のように文字クラスで書け。"
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
