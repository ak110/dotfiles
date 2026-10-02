# ruff: noqa: F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
r"""PreToolUse統合フックのうち、Bashコマンドを遮断する条件の判定。"""

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
    extract_bash_invocations,
    split_bash_segments,
)
from agent_toolkit._common.shell_tokens import strip_redirections


if TYPE_CHECKING:
    from agent_toolkit._hooks.pretooluse.dispatch import (
        _ExecutionSegment,
        _extract_execution_segments,
    )
    from agent_toolkit._hooks.pretooluse.notices import (
        _block_notice,
        _llm_notice,
    )
    from agent_toolkit._hooks.notice import _WARN_TAG


# --- Bash: パターン一致によるプロセス終了の検出 ---

_GIT_GREP_VALUED_OPTIONS = frozenset(
    {"-A", "-B", "-C", "-m", "--after-context", "--before-context", "--context", "--max-count", "--max-depth", "--threads"}
)
"""`git grep -h`の必須値付きオプション（git 2.43.0の確認。パターン用は別集合）。"""


def _attached_short_value_option(token: str, valued: Iterable[str]) -> str | None:
    """値を密着させた短縮オプションの形であれば、そのオプション名を返す。

    `-A14`のように値を空白なしで連結した形は対象コマンドが受理する1つのトークンである。
    短縮オプションの連結として1文字ずつ受理集合に含まれるか判定すると、値の各文字が受理集合に無いという判定になる。
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


def _git_grep_argument_layout(arguments: Sequence[str]) -> tuple[set[int], int | None]:
    """`git grep`の引数列から、検索パターンの位置の集合と、revision・パスの区切り`--`の位置を返す。

    gitの受理形式（git 2.47.3の実行で確認）は、最初の`--`より前に`-e`・`-f`か位置引数の検索パターンがあるかで分かれる。

    - ある場合: 最初の`--`がrevision・パスの区切りであり、その後ろは全てrevisionとパスとして読まれる
    - 無い場合: 最初の`--`はオプションの終端であり、直後の1トークンが検索パターンになる。
      その後ろに現れる最初の`--`がrevision・パスの区切りになる

    パターンの位置には`-e`の値と位置引数の検索パターンを含め、`-f`が読むファイル名は含めない。
    `-ie`のように`-e`を他の短縮オプションと束ねた形は`-e`として扱わない。
    """
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
    if index >= len(arguments):
        return indices, None
    if pattern_seen:
        return indices, index
    pattern_index = index + 1
    if pattern_index >= len(arguments):
        return indices, None
    indices.add(pattern_index)
    separator = next((i for i in range(pattern_index + 1, len(arguments)) if arguments[i] == "--"), None)
    return indices, separator


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

    対象の所有権を確認できないパターン一致の一括終了は他者のプロセスを停止する危険があるため禁止する。
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
            fix=(
                "自身が起動しPIDで特定したプロセスに対して`kill <PID>`を使う。"
                "検索語として使う場合は`rg`・`grep`・`git grep`・`git log -S`の引数へリテラルで書くか、"
                "`p[k]ill`のように文字クラスで書く。"
            ),
        ),
        file=sys.stderr,
    )
    return True


def _git_subcommand_tokens(segment: _ExecutionSegment) -> tuple[str, tuple[str, ...]] | None:
    """`git`区間のサブコマンド名と、そのサブコマンド以降の引数を返す。"""
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


# --- Bash: `git rev-parse --short`への複数revision ---

_GIT_REV_PARSE_VALUED_OPTIONS = frozenset({"--default", "--prefix", "--git-path", "--resolve-git-dir"})
"""`man git-rev-parse`の値を別引数で取るオプション（git 2.43.0）。"""


def _rev_parse_short_revisions(arguments: Sequence[str]) -> list[str] | None:
    """`git rev-parse`の引数が`--short`を持つ場合に、revisionとして渡された引数を返す。

    `--`以降はパスとして扱い、revisionに数えない。リダイレクトの演算子と対象も数えない。
    `--short`を持たない場合はNoneを返す。
    """
    has_short = False
    revisions: list[str] = []
    skip_value = False
    for token in strip_redirections(arguments):
        if skip_value:
            skip_value = False
            continue
        if token == "--":
            break
        if token in _GIT_REV_PARSE_VALUED_OPTIONS:
            skip_value = True
            continue
        if token == "--short" or token.startswith("--short="):
            has_short = True
            continue
        if token.startswith("-"):
            continue
        revisions.append(token)
    return revisions if has_short else None


def _warn_git_rev_parse_short_multiple(command: str) -> str | None:
    """`git rev-parse --short`へ2つ以上のrevisionを渡すコマンドへ警告本文を返す。

    同コマンドは1回に1つのrevisionだけを受理し、複数を渡すと`fatal: Needed a single revision`で失敗する。
    条文で定めた後も同じ失敗が反復したため、実行の直前に判定する。
    結果は再実行で是正できるため、遮断せず警告に留める。
    """
    for invocation in extract_bash_invocations(command):
        if not invocation.arguments_known:
            continue
        segment = invocation.segment
        subcommand = _git_subcommand_tokens(segment)
        if subcommand is None or subcommand[0] != "rev-parse":
            continue
        revisions = _rev_parse_short_revisions(subcommand[1])
        if revisions is not None and len(revisions) >= 2:
            return _llm_notice(
                f"`git rev-parse --short`へ{len(revisions)}つのリビジョン（{'、'.join(revisions)}）を渡している。"
                "同コマンドは1回に1つのリビジョンだけを受理し、実行されると"
                "`fatal: Needed a single revision`で終了コード128になる。"
                "`&&`で連結した後段は実行されず、後に表示する`$?`は後段ではなくこの失敗を示し得る。"
                "連結した結果を判断へ使う前に、各区間が実行されたかを確かめる。",
                tag=_WARN_TAG,
                fix="リビジョンごとに`git rev-parse --short=7 <revision>`を個別に実行し、入力と出力の対応を保つ。",
                removable_cause=True,
            )
    return None


# --- Bash: オプション終端`--`の後ろに置いたCLI自身のオプション ---

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


def _pattern_command_slot(arguments: Sequence[str]) -> set[int]:
    """`rg`・`grep`系で、`--`の直後を検索パターンとして読む場合にその位置を返す。

    `-e`・`--regexp`・`-f`・`--file`（`=`や`-eX`の連結形を含む）を`--`より前に置く場合は、
    `--`の後ろが全てパスとして読まれるため空集合を返す（ripgrep 15.2.0とGNU grepの実行で確認）。
    """
    if "--" not in arguments:
        return set()
    terminator = list(arguments).index("--")
    for token in arguments[:terminator]:
        if token.split("=", 1)[0] in _OPTION_TERMINATOR_PATTERN_OPTIONS:
            return set()
        if _attached_short_value_option(token, ("-e", "-f")) is not None:
            return set()
    return {terminator + 1}


def _check_bash_option_after_terminator(command: str) -> bool:
    """オプション終端`--`の後ろへCLI自身のオプションを置いたコマンドを遮断する。

    `--`の後ろは全てデータとして扱われるため、後ろへ置いた`--glob`などは`rg`・`grep`系・`git grep`では
    存在しないパスとして失敗し、`git log`・`git diff`・`git show`ではエラーを出力せずにパス指定として扱われ、誤った結果を返す。
    条文で配置を定めた後も同じ誤りが反復したため、実行の直前に判定する。
    遮断とする根拠は`agent-toolkit:writing-standards`の`references/claude-hooks.md`「遮断・警告フックの成立条件」にある。
    外側に所属する既知の引数だけを判定し、置換内の語と展開結果が未確定の引数はオプションとして扱わない。
    遮断で失うのはコマンド1回の発行だけである。
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
            found = _options_after_terminator(arguments, excluded=_pattern_command_slot(arguments))
            label = name
        else:
            subcommand = _git_subcommand_tokens(segment)
            if subcommand is None or subcommand[0] not in _OPTION_TERMINATOR_GIT_SUBCOMMANDS:
                continue
            arguments = list(strip_redirections(subcommand[1]))
            excluded: set[int] = set()
            if subcommand[0] == "grep":
                pattern_indices, separator = _git_grep_argument_layout(arguments)
                excluded = pattern_indices | ({separator} if separator is not None else set())
            found = _options_after_terminator(arguments, excluded=excluded)
            label = f"git {subcommand[0]}"
        if not found:
            continue
        print(
            _block_notice(
                f"blocked: `{label}`のオプション終端`--`の後ろにオプション（{'、'.join(found)}）がある。"
                "`--`の後ろは全てデータとして扱われるため、これらはオプションではなくパスとして解釈され、"
                "存在しないパスとして失敗するか、エラーを出力せずに結果を限定する。",
                fix=(
                    "そのコマンド自身のオプションを`--`より前へ移し、`--`の後ろには検索パターンとパスだけを置いて再実行する。"
                    "`-`で始まるパスを渡す場合は`./`を前置する。"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- Bash: atkの結果を受領できない出力接続 ---


def _check_bash_atk_output_loss(command: str) -> bool:
    """静的に確定したatkの出力パイプとwaitの背景化・標準出力破棄を遮断する。

    Bashが返す出力から結果と終了状態を失う反復を、その場で書き直せる入力で止める。
    引数のリテラルやheredoc本文、未知の実行位置は判定せず、ホストが管理する背景実行は通す。
    """
    for invocation in extract_bash_invocations(command):
        tokens = invocation.segment.tokens
        if not invocation.segment.resolved or not tokens or pathlib.PurePosixPath(tokens[0]).name != "atk":
            continue
        is_wait = tokens[1:3] == ("agents", "wait")
        causes: list[str] = []
        if is_wait and invocation.background:
            causes.append("シェルの`&`による背景化")
        if is_wait and invocation.stdout_discarded:
            causes.append("標準出力の`/dev/null`への破棄")
        if invocation.output_pipe:
            causes.append("atkから後段へ出力を渡すパイプ")
        if not causes:
            continue
        label = "atk agents wait" if is_wait else "atk"
        print(
            _block_notice(
                f"blocked: `{label}`の結果と終了状態を直接受領できない入力（{'、'.join(causes)}）を検出した。",
                fix=(
                    "`atk agents wait`は`&`と標準出力の破棄を外して単独で発行する。"
                    "`Claude Code`で背景で待つ場合は`Bash`の`run_in_background`を使い、返されたタスクの識別子で結果を受領する。"
                    "`atk`の出力量はサブコマンドが公開する対象限定で減らす。保存先を指定する必要がある場合は"
                    "`--output-file`を使い、保存した本文の選別は別の呼び出しで行う。"
                ),
            ),
            file=sys.stderr,
        )
        return True
    return False


# --- Bash: WindowsのGit BashでPATHへ加えるドライブ文字形式の要素の検出 ---

_SHELL_ASSIGNMENT_PATTERN = re.compile(r"^([A-Za-z_]\w*)=(.*)$", re.DOTALL)
_DRIVE_LETTER_PATH_PATTERN = re.compile(r"[A-Za-z]:[/\\]")
_PATH_ELEMENT_VARIABLE_PATTERN = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")


def _segment_assignments(raw_tokens: Sequence[str]) -> list[tuple[str, str]]:
    """区間の元トークン列から、先頭の代入の並びと`export`の引数にある代入を順に返す。

    単独の代入文とコマンド前置の代入はいずれも先頭の`KEY=VALUE`の並びに現れる。
    `export`の後の`KEY=VALUE`もシェル変数への代入として扱う。
    """
    assignments: list[tuple[str, str]] = []
    index = 0
    while index < len(raw_tokens) and (match := _SHELL_ASSIGNMENT_PATTERN.match(raw_tokens[index])):
        assignments.append((match.group(1), match.group(2)))
        index += 1
    if index < len(raw_tokens) and raw_tokens[index] == "export":
        for token in raw_tokens[index + 1 :]:
            if match := _SHELL_ASSIGNMENT_PATTERN.match(token):
                assignments.append((match.group(1), match.group(2)))
    return assignments


def _drive_letter_path_elements(value: str, assigned: dict[str, str]) -> list[str]:
    r"""PATHの値のうち、ドライブ文字形式で始まる要素を説明用の文字列で返す。

    要素の先頭（値の先頭かコロンの直後）がリテラルの`C:/`・`C:\\`形式である要素と、
    同じコマンドの中でそれより前にドライブ文字形式の値を代入した変数の展開で始まる要素を対象とする。
    """
    found: list[str] = []
    for position in range(len(value)):
        if position > 0 and value[position - 1] != ":":
            continue
        rest = value[position:]
        if _DRIVE_LETTER_PATH_PATTERN.match(rest):
            end = rest.find(":", 2)
            found.append(rest if end < 0 else rest[:end])
            continue
        variable = _PATH_ELEMENT_VARIABLE_PATTERN.match(rest)
        if variable is None:
            continue
        name = variable.group(1) or variable.group(2)
        assigned_value = assigned.get(name)
        if assigned_value is not None and _DRIVE_LETTER_PATH_PATTERN.match(assigned_value):
            found.append(f"${name}（代入値: {assigned_value}）")
    return found


def _warn_windows_drive_letter_path(command: str, *, is_codex: bool) -> str | None:
    """WindowsのGit BashでPATHへドライブ文字形式の要素を加えるコマンドへ警告本文を返す。

    Git Bash（MSYS2）のPATHはコロン区切りでドライブ文字形式を変換しないため、`C:/x`は`C`と`/x`の2要素に分かれ、
    意図したディレクトリが検索されない。その結果を根拠に結論を下す前に気付けるよう、実行の直前に判定する。

    判定の結論は警告とする。根拠は`agent-toolkit:writing-standards`の`references/claude-hooks.md`
    「遮断・警告フックの成立条件」にある。誤ったPATHはコマンドを失敗させずに誤った結果を返し、
    その結果が誤った結論の根拠になる。判定はコマンド文字列から機械的に確定でき、実行を止めないため誤検出の費用も小さい。
    影響はそのコマンドのプロセス環境に閉じ、正しい形式で再実行すれば是正できるため遮断はしない。
    反復しても母集団の欠落や工程の停止を招かないため、反復時の昇格もしない。

    対象はWindows上のClaude CodeのBashツールに限る。CodexはPowerShell（PATHはセミコロン区切り）で
    シェルを実行し、Windows以外ではドライブ文字形式のパスが通常の操作で現れない。
    継承した環境変数の展開、コマンド置換および別のBash呼び出しで代入した変数は、値をhookの入力から
    確定できないため判定しない。
    """
    if sys.platform != "win32" or is_codex:
        return None
    assigned: dict[str, str] = {}
    found: list[str] = []
    for segment in _extract_execution_segments(command):
        for name, value in _segment_assignments(segment.raw_tokens):
            if name == "PATH":
                found.extend(_drive_letter_path_elements(value, assigned))
            assigned[name] = value
    if not found:
        return None
    return _llm_notice(
        f"`PATH`へドライブ文字形式の要素（{'、'.join(found)}）を加えている。"
        "`Git Bash`の`PATH`はコロン区切りのため、ドライブ文字形式の要素は`C`と`/...`の2要素に分かれ、"
        "そのディレクトリは検索されない。コマンドは実行済みであり、`PATH`に依存した結果"
        "（`command -v`の出力、実行されたプログラム、計測値）は意図した構成のものではない。",
        tag=_WARN_TAG,
        fix="`/c/Users/...`の形で書くか、`\"$(cygpath -u '<Windows形式のパス>')\"`で変換した値を`PATH`へ加えて再実行する。",
        removable_cause=True,
    )
