"""規範の見出し抽出とCodexの大量読取に、全文を受信できる取得方法を案内する。

Codexでは出力予算を超えた取得が本文の省略を招くため、容量と行範囲の分割取得の手段を警告する。
容量警告は、残りを継続取得できるClaude Codeを対象外とする。
規範の見出し正規表現による範囲抽出は、両ホストで全文取得を案内する。

内側コマンドと外側セルの予算をhookから確定できないため、通常の読取を継続する。
全文と終了状態を保持できる予算なら標準読取を使え、省略時は`sed -n '<開始行>,<終了行>p'`の分割取得へ移れる。
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import ast
lazy import re
lazy import shlex
lazy import stat
lazy from collections.abc import Iterator, Sequence

from agent_toolkit._hooks.notice import formatter
lazy from agent_toolkit._common import host_homes, read_triggers, shell_cwd
lazy from agent_toolkit._common.bash_invocations import is_unknown_word
lazy from agent_toolkit._common.shell_tokens import ShellWord
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks import plugin_resources
lazy from agent_toolkit._hooks.pretooluse.read_acquisition import codex_acquisition

# Codexの配布設定の出力上限`tool_output_token_limit = 20000`（トークン）と、
# agent-toolkitのMarkdownで測った1トークンあたりバイト数の最小値3.10の積は約62,000バイトである。
# 実行セルが本文へ付加する分の余裕を取り、48KiBを閾値とする。
# 測定の記録と再検証手段は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_hooks/pretooluse/large_reads.py：全文取得の閾値：2026年9月29日」にある。
_DEFAULT_BYTE_THRESHOLD = 48 * 1024
_BYTE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_BYTES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_SEARCH_COMMANDS = frozenset({"rg", "grep", "egrep", "fgrep"})
_notice = formatter("pretooluse")


def _large_warning(body: str, *, fix: str, subject: str = "") -> str:
    return _notice(body, tag="warn", fix=fix, removable_cause=False, subject=subject)


def _byte_threshold() -> int:
    """環境指定が正の整数なら採用し、それ以外は指定を省略した場合の値を返す。"""
    try:
        threshold = int(os.environ.get(_BYTE_THRESHOLD_ENV, str(_DEFAULT_BYTE_THRESHOLD)))
    except ValueError:
        return _DEFAULT_BYTE_THRESHOLD
    return threshold if threshold > 0 else _DEFAULT_BYTE_THRESHOLD


@dataclass(frozen=True)
class _ReadPlan:
    line_count: int
    byte_count: int


def _measure_and_plan(path: pathlib.Path) -> _ReadPlan | None:
    """警告の判定と対象の説明に必要な容量だけを数える。"""
    if not path.is_file():
        return None
    line_count = byte_count = 0
    try:
        with path.open("rb") as source:
            for line in source:
                line_count += 1
                byte_count += len(line)
    except OSError:
        return None
    return _ReadPlan(line_count, byte_count)


def _resolve_path(value: str, cwd: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else pathlib.Path(cwd) / path


def _display_operands(name: str, tokens: Sequence[str]) -> tuple[str, ...]:
    """既知の本文表示オプションとその値を除き、終端後はファイル名として扱う。"""
    flags = {
        "cat": "AbeEnstTuv",
        "less": "aABcCdeEfFgGiIJKLmMnNqQrRsSuUwWX~",
        "more": "deflpcsu",
    }[name]
    values = {"less": "bjhoxzPp", "more": "n"}.get(name, "")
    long_flags = {
        "cat": {"show-all", "number-nonblank", "show-ends", "number", "squeeze-blank", "show-tabs", "show-nonprinting"},
        "less": {
            "LINE-NUMBERS",
            "line-numbers",
            "chop-long-lines",
            "RAW-CONTROL-CHARS",
            "raw-control-chars",
            "quit-if-one-screen",
            "no-init",
            "ignore-case",
            "IGNORE-CASE",
            "squeeze-blank-lines",
        },
        "more": {"silent", "logical", "no-pause", "print-over", "clean-print", "squeeze", "plain", "exit-on-eof"},
    }[name]
    long_values = {
        "less": {"buffers", "jump-target", "max-back-scroll", "max-forw-scroll", "tabs", "window", "pattern", "prompt"},
        "more": {"lines"},
    }.get(name, set())
    operands: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == "--":
            operands.extend(tokens[index:])
            break
        if token.startswith("--"):
            option, equals, value = token[2:].partition("=")
            if option in long_flags and not equals:
                continue
            if option not in long_values:
                return ()
            if not equals:
                if index >= len(tokens):
                    return ()
                index += 1
            elif not value:
                return ()
        elif token.startswith("-"):
            if token == "-":
                return ()
            if name == "more" and token[1:].isdigit():
                continue
            for position, option in enumerate(token[1:], 1):
                if option in values:
                    if position == len(token) - 1:
                        if index >= len(tokens):
                            return ()
                        index += 1
                    break
                if option not in flags:
                    return ()
        elif token.startswith("+") and name in {"less", "more"}:
            return ()
        else:
            operands.append(token)
    if any(any(marker in operand for marker in ("<", ">", "$(", "`")) for operand in operands):
        return ()
    return tuple(operands)


def _read_operands(tokens: Sequence[str], *, include_partial: bool, first_line_only: bool = False) -> tuple[str, ...]:
    """全文取得のファイル引数を返す。`include_partial`が真なら、帰属行の配送だけが対象とする範囲取得のファイル引数も含める。

    `first_line_only`が真なら、行範囲の取得は1行目から始まるものだけを含める。
    """
    if not tokens:
        return ()
    name = pathlib.PurePath(tokens[0]).name
    if name in _FULL_READ_COMMANDS:
        return _display_operands(name, tokens)
    sed = sed_read_range(tokens)
    if sed is not None:
        operand, start, end = sed
        if first_line_only and start > 1:
            return ()
        return (operand,) if include_partial or (start == 1 and end is None) else ()
    if len(tokens) == 3 and name == "awk":
        program = "".join(tokens[1].split())
        if program in {"{print}", "{print$0}"}:
            return (tokens[2],)
    return ()


def arguments_are_data(tokens: Sequence[str]) -> bool:
    """読取・検索の解析が、全引数をプログラムとして実行しないデータとして扱う呼出しかを返す。

    本文表示（`cat`・`less`・`more`）は表示の被演算子を確定できる場合、`head`・`tail`は常に、
    `sed`・`awk`は読取の解析が全文取得と確定した形だけを対象にする。検索は、外部プログラムを起動する
    `rg --pre`を除く。他のコマンドは引数の扱いを解析できないため偽を返す。
    """
    if not tokens:
        return False
    name = pathlib.PurePath(tokens[0]).name
    if name in _SEARCH_COMMANDS:
        return not any(token == "--pre" or token.startswith(("--pre=", "--pre-glob")) for token in tokens[1:])
    if name in {"head", "tail"}:
        return True
    return bool(_read_operands(tokens, include_partial=True))


def sed_read_range(tokens: Sequence[str]) -> tuple[str, int, int | None] | None:
    """静的な`sed -n '<開始行>,<終了行>p' [--] <パス>`のパスと1始まりの行範囲を返す。

    終了行の`None`は末尾までを表す。書込や置換を含み得る他のスクリプトと複数ファイルの指定は対象外とする。
    """
    if len(tokens) not in {4, 5} or pathlib.PurePath(tokens[0]).name != "sed" or tokens[1] != "-n":
        return None
    if len(tokens) == 5 and tokens[3] != "--":
        return None
    operand = tokens[-1]
    if len(tokens) == 4 and operand.startswith("-"):
        return None
    matched = re.fullmatch(r"(?:(\d+)(?:,(\d+|\$))?)?p", tokens[2])
    if matched is None:
        return None
    if matched[1] is None:
        return operand, 1, None
    start = int(matched[1])
    if start < 1:
        return None
    if matched[2] is None:
        return operand, start, start
    if matched[2] == "$":
        return operand, start, None
    # sedは終了行が開始行より前なら開始行だけを出力する。
    return operand, start, max(start, int(matched[2]))


def is_dynamic_read_operand(operand: str) -> bool:
    """引用の外側で展開する語を、実在するリテラルの読取対象と区別する。"""
    unquoted = operand.unquoted if isinstance(operand, ShellWord) else operand
    return (
        is_unknown_word(operand)
        or any(marker in unquoted for marker in ("$", "`", "<", ">", "*", "?", "["))
        or re.search(r"\{[^{}]*,[^{}]*\}|\{(?:-?\d+\.\.-?\d+|[a-zA-Z]\.\.[a-zA-Z])(?:\.\.-?\d+)?\}", unquoted) is not None
    )


def bash_read_paths(
    command: str, cwd: str, *, include_partial: bool = False, first_line_only: bool = False, include_piped: bool = False
) -> Iterator[tuple[pathlib.Path, ...]]:
    """読取の解析とcwd・パスの解決を共有し、コマンドごとにファイル群を返す。

    `first_line_only`は`_read_operands`の同名の指定へ渡す。
    `include_piped`は警告候補専用で、加工後の出力を取得成功や容量判定の根拠にしない。
    """
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        # output_redirectedにはパイプ接続も含まれる。警告用の候補では、その接続だけを許容する。
        if invocation.captured or invocation.output_redirected and not (include_piped and invocation.output_pipe):
            continue
        # 読取候補の特定（`include_partial`）では、無関係な引数が未知でも既知のファイル引数を候補に残す。
        # 出力全体の順序と範囲を使う用途（容量の判定など）は、全引数が確定した呼出しだけを使う。
        if not invocation.arguments_known and not include_partial:
            continue
        tokens = invocation.segment.tokens
        paths = []
        for operand in _read_operands(tokens, include_partial=include_partial, first_line_only=first_line_only):
            if is_dynamic_read_operand(operand):
                continue
            if not pathlib.Path(operand).expanduser().is_absolute() and not current.resolved:
                continue
            base = current.path
            paths.append(_resolve_path(operand, base))
        if paths:
            yield tuple(paths)


def _is_session_record(path: pathlib.Path, *, directory: bool = False) -> bool:
    """既存ホーム解決で得た原記録の根と、JSONL・検索ディレクトリを対応付ける。"""
    resolved = path.resolve()
    roots = (host_homes.claude_config_dir() / "projects", host_homes.codex_home() / "sessions")
    return any(resolved.is_relative_to(root.resolve()) for root in roots) and (
        resolved.suffix == ".jsonl" or directory and (not resolved.suffix or resolved.is_dir())
    )


def _content_operands(tokens: Sequence[str], *, pattern: bool) -> tuple[str, ...]:
    """検索語・プログラムとオプション値を、本文取得のファイル引数から除く。"""
    values = {
        "-e",
        "-f",
        "-A",
        "-B",
        "-C",
        "-m",
        "-g",
        "-t",
        "-T",
        "--regexp",
        "--file",
        "--expression",
        "--glob",
        "--iglob",
        "--type",
        "--type-not",
        "--after-context",
        "--before-context",
        "--context",
        "--max-count",
        "--encoding",
        "--threads",
        "--replace",
        "--color",
        "--colors",
        "--include",
        "--exclude",
        "--exclude-dir",
        "--lines",
        "--bytes",
    }
    if pathlib.PurePath(tokens[0]).name in {"head", "tail"}:
        values.update({"-n", "-c"})
    operands: list[str] = []
    has_pattern = not pattern
    index = 1
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token == "--":
            operands.extend(tokens[index:])
            break
        option = token.split("=", 1)[0]
        if option in values:
            has_pattern = has_pattern or option in {"-e", "-f", "--regexp", "--file", "--expression"}
            if "=" not in token:
                index += 1
        elif token.startswith("-"):
            if not token.startswith("--"):
                for position, flag in enumerate(token[1:], 1):
                    if f"-{flag}" in values:
                        has_pattern = has_pattern or flag in "ef"
                        if position == len(token) - 1:
                            index += 1
                        break
        else:
            operands.append(token)
    return tuple(operands if has_pattern else operands[1:])


def python_file_paths(code: str, *, writing: bool = False) -> list[str]:
    """直列に並ぶリテラルと単純代入を解析し、Pythonコードが読み書きする静的なファイルパスを返す。読取と書込の判定がこの解析を共用する。"""
    try:
        module = ast.parse(code)
    except SyntaxError:
        return []
    values: dict[str, str] = {}
    path_variables: set[str] = set()
    paths: list[str] = []
    methods = {"write_text", "write_bytes"} if writing else {"read_text", "read_bytes", "open"}

    def matches_mode(node: ast.AST) -> bool:
        mode = literal(node) or ""
        return any(char in mode for char in "wax+") if writing else mode in {"r", "rb", "rt"}

    def is_path(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Name)
            and node.id in path_variables
            or isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == "Path"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "Path"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pathlib"
            )
        )

    def literal(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return values.get(node.id)
        if isinstance(node, ast.Call) and node.args and is_path(node):
            return literal(node.args[0])
        return None

    def inspect(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
                mode = (
                    node.args[1]
                    if len(node.args) > 1
                    else next((item.value for item in node.keywords if item.arg == "mode"), ast.Constant("r"))
                )
                if matches_mode(mode) and (path := literal(node.args[0])) is not None:
                    paths.append(path)
            elif isinstance(node.func, ast.Attribute) and is_path(node.func.value) and node.func.attr in methods:
                mode = (
                    node.args[0]
                    if node.args
                    else next((item.value for item in node.keywords if item.arg == "mode"), ast.Constant("r"))
                )
                if (node.func.attr != "open" or matches_mode(mode)) and (path := literal(node.func.value)) is not None:
                    paths.append(path)
        for child in ast.iter_child_nodes(node):
            inspect(child)

    def statements(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                if node.value is not None:
                    inspect(node.value)
                value = literal(node.value) if node.value is not None else None
                path_value = node.value is not None and is_path(node.value)
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        values.pop(target.id, None)
                        path_variables.discard(target.id)
                        if value is not None:
                            values[target.id] = value
                            if path_value:
                                path_variables.add(target.id)
            elif isinstance(node, ast.Expr):
                inspect(node.value)
            elif isinstance(node, ast.With):
                for item in node.items:
                    inspect(item.context_expr)
                statements(node.body)

    statements(module.body)
    return paths


def _script_display_operands(tokens: Sequence[str]) -> tuple[str, ...]:
    """既知の表示スクリプトだけから入力を得る。実行せず、範囲の被覆も証明しない。

    sedはアドレス付きprint、awkは条件・代入・print・exitなどの表示式を対象にする。
    オプション付きの未知構文、ファイル出力、外部実行は候補から除く。
    """
    name = pathlib.PurePath(tokens[0]).name
    start = 2 if name == "sed" else 1
    if len(tokens) <= start + 1 or name == "sed" and tokens[1] != "-n":
        return ()
    program = tokens[start]
    if is_unknown_word(program) or program.startswith("-"):
        return ()
    if name == "sed":
        address = r"(?:\d+|\$|/(?:\\.|[^/\\\n])*/)"
        if re.fullmatch(rf"(?:{address}(?:,{address})?)?p", program) is None:
            return ()
    else:
        # awk全体の実行解析は行わない。出力先変更・getline・関数呼出しを含む式は対象外とする。
        if re.search(r"[<>|@`]|\bgetline\b|\b[A-Za-z_]\w*\s*\(", program):
            return ()
        expression = re.sub(r'/(?:\\.|[^/\\\n])*/|"(?:\\.|[^"\\\n])*"', "0", program)
        if not expression.strip() or re.fullmatch(r"[\w\s${}();=!&+*/%.,?:\[\]-]+", expression) is None:
            return ()
    operands = tokens[start + 1 :]
    if operands[0] == "--":
        operands = operands[1:]
    elif any(operand.startswith("-") for operand in operands):
        return ()
    # awkのname=valueはファイル引数ではない。代入による入力先の変更も推測しない。
    if any(operand == "-" or name == "awk" and re.match(r"[A-Za-z_]\w*=", operand) for operand in operands):
        return ()
    return tuple(operands)


def _display_read_operands(tokens: Sequence[str]) -> tuple[str, ...]:
    if not tokens or is_unknown_word(tokens[0]):
        return ()
    name = pathlib.PurePath(tokens[0]).name
    if name in _SEARCH_COMMANDS:
        if any(option in tokens for option in ("--files", "--help", "--version")):
            return ()
        return _content_operands(tokens, pattern=True)
    if name in {"head", "tail"}:
        return _content_operands(tokens, pattern=False)
    if name in {"sed", "awk"}:
        return _script_display_operands(tokens)
    return _read_operands(tokens, include_partial=True)


def _terminal_display_operands(command: str, cwd: str) -> Iterator[tuple[str, shell_cwd.CwdResolution]]:
    """内容を端末へ表示する呼出しの被演算子と、その実行位置を返す。捕捉・出力先変更は除く。"""
    invocations = list(shell_cwd.bash_invocation_cwds(command, cwd))
    for index, (invocation, current) in enumerate(invocations):
        if invocation.captured or invocation.output_redirected and not invocation.output_pipe:
            continue
        if invocation.output_pipe:
            downstream = index + 1
            while downstream < len(invocations) and invocations[downstream][0].output_pipe:
                downstream += 1
            if downstream < len(invocations) and invocations[downstream][0].output_redirected:
                continue
        for operand in _display_read_operands(invocation.segment.tokens):
            yield operand, current


def display_read_paths(command: str, cwd: str) -> list[pathlib.Path]:
    """内容を表示する静的なファイル引数を返す。パイプによる部分表示も案内の対象にする。

    `bash_read_paths`が証明する全文受領とは用途が異なる。編集、ファイルへの出力と
    ディレクトリ検索を除き、この結果を資料の取得証拠に使わない。
    """
    paths = []
    for operand, current in _terminal_display_operands(command, cwd):
        if is_unknown_word(operand):
            continue
        if not pathlib.Path(operand).expanduser().is_absolute() and not current.resolved:
            continue
        path = _resolve_path(operand, current.path if current.resolved else cwd)
        if path.is_file():
            paths.append(path.resolve())
    return list(dict.fromkeys(paths))


def has_dynamic_display_operand(command: str, cwd: str) -> bool:
    """内容を表示する呼出しに、変数展開などで静的に確定できない被演算子があるかを返す。

    `display_read_paths`の候補なしと区別するために使う。確定できない対象を推測して解決せず、
    資料の取得証拠にも使わない。
    """
    return any(is_unknown_word(operand) for operand, _current in _terminal_display_operands(command, cwd))


def _search_metadata_only(tokens: Sequence[str]) -> bool:
    """検索語とオプション値を除き、本文を出さない検索の指定を判定する。"""
    name = pathlib.PurePath(tokens[0]).name
    # rgの-Lは--followであり本文を返す。grep系列の-Lだけがファイル名を返す。
    metadata_flags = "lc" if name == "rg" else "lLc"
    valued_flags = "efgtrTABCMmj" if name == "rg" else "efABCmdD"
    long_values = {
        "--regexp",
        "--file",
        "--glob",
        "--iglob",
        "--type",
        "--type-not",
        "--include",
        "--exclude",
        "--exclude-dir",
        "--after-context",
        "--before-context",
        "--context",
        "--max-count",
        "--encoding",
        "--threads",
        "--replace",
        "--color",
        "--colors",
        "--max-columns",
        "--max-depth",
        "--max-filesize",
        "--sort",
        "--sortr",
        "--pre",
        "--pre-glob",
        "--ignore-file",
        "--type-add",
        "--type-clear",
        "--label",
        "--binary-files",
        "--devices",
        "--directories",
    }
    skip_value = False
    for token in tokens[1:]:
        if skip_value:
            skip_value = False
            continue
        if token == "--":
            break
        if token in {
            "--files",
            "--files-with-matches",
            "--files-without-match",
            "--count",
            "--count-matches",
            "--help",
            "--version",
        }:
            return True
        if token.startswith("--"):
            skip_value = "=" not in token and token in long_values
            continue
        if token.startswith("-"):
            for index, flag in enumerate(token[1:], 1):
                if flag in metadata_flags:
                    return True
                if flag in valued_flags:
                    skip_value = index == len(token) - 1
                    break
    return False


def _bash_session_record_paths(command: str, cwd: str) -> list[pathlib.Path]:
    """実行位置と静的引数だけから、原記録の読取・本文検索・解析入力を得る。"""
    found: list[pathlib.Path] = []
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.segment.tokens
        # 検索語や追加引数が未知でも、静的な入力先は参照案内の対象に残す。コマンド名が未知なら判定しない。
        if not tokens or is_unknown_word(tokens[0]):
            continue
        name = pathlib.PurePath(tokens[0]).name
        search = name in _SEARCH_COMMANDS
        operands = list(_display_read_operands(tokens))
        if search:
            if _search_metadata_only(tokens):
                continue
            operands = list(_content_operands(tokens, pattern=True))
            if not operands and current.resolved and invocation.arguments_known:
                operands = [current.path]
        elif name in {*_FULL_READ_COMMANDS, "head", "tail"} and not operands:
            operands = list(_content_operands(tokens, pattern=False))
        elif is_python_token(name):
            if "-c" in tokens:
                position = tokens.index("-c")
                operands = python_file_paths(tokens[position + 1]) if position + 1 < len(tokens) else []
            elif "--help" not in tokens and "--version" not in tokens:
                operands = list(tokens[2:])
        elif name in {"jq", "node", "ruby", "perl"}:
            operands = list(tokens[2:])
        for operand in operands:
            if is_unknown_word(operand):
                continue
            if not pathlib.Path(operand).expanduser().is_absolute() and not current.resolved:
                continue
            path = _resolve_path(operand, current.path if current.resolved else cwd)
            if _is_session_record(path, directory=search):
                found.append(path)
    return found


def session_record_reference_warning(tool_name: str, tool_input: dict, cwd: str) -> str | None:
    """両ホストで静的に確定した原記録参照へ、由来を保持する専用照会を案内する。"""
    paths: list[pathlib.Path] = []
    if tool_name in {"Read", "Grep"}:
        value = tool_input.get("file_path" if tool_name == "Read" else "path", cwd if tool_name == "Grep" else "")
        if isinstance(value, str) and value:
            path = _resolve_path(value, cwd)
            if _is_session_record(path, directory=tool_name == "Grep"):
                paths.append(path)
    elif tool_name == "Bash" and isinstance(tool_input.get("command"), str):
        paths = _bash_session_record_paths(tool_input["command"], cwd)
    if not paths:
        return None
    reference = plugin_resources.skill_reference("review-session", "references/session-records.md")
    return _notice(
        "セッションの原記録の本文を、汎用ツールで直接参照しようとしている。\n対象: " + "、".join(str(path) for path in paths),
        tag="warn",
        fix=(
            "`atk run-script session-review-evidence --`へ、"
            "ファイルは`--transcript <原記録の絶対パス>`、"
            "本文の検索は`--grep <正規表現>`、単一の`--transcript`では`--fixed-string <文字列>`も使える。"
            "指定位置は`--detail <record:line>`、"
            "ツール入力は`--tool-calls --tool <名前> --input-regex <正規表現>`を渡せ。"
            "記録をまたぐ文字列の所在は`rg -l -F -- '<文字列>' <記録のディレクトリ>`で列挙し、"
            "各パスを`--transcript`へ渡して本文を照会せよ。"
            "期間と親セッションの一覧には`--catalog-claude-project <ディレクトリ>`か`--catalog-codex-history <ディレクトリ>`へ"
            "`--since <時刻> --observation-boundary <時刻>`を加えよ。"
            "発話後の呼出順は`--user-events`とツール一覧、または`--bundle`の抽出済み`conversation.jsonl`から集計せよ。"
            f"入力の選択は{reference}に従い、抽出済みの結果を後処理せよ。"
        ),
        removable_cause=False,
        escalate_on_repeat=False,
    )


def _large_read_notice(path: pathlib.Path, plan: _ReadPlan, byte_threshold: int) -> str:
    if _is_session_record(path):
        return _large_warning(
            f"原セッション記録の全文取得の出力量を確認せよ（{plan.byte_count}バイト、"
            f"通知の閾値: {byte_threshold}バイト）: {path}",
            fix="まず取得の必要性と範囲を見直せ。目的に不要なら取得を中止せよ。必要な場合は、"
            "`atk run-script session-review-evidence -- --transcript <原記録の絶対パス>`に、目的に応じた照会条件を加えよ。"
            "`agent-toolkit:review-session`の`references/session-records.md`で入力と照会モードを選べ。",
        )
    return _large_warning(
        f"{plan.line_count}行、{plan.byte_count}バイトの全文取得の出力量を確認せよ"
        f"（通知の閾値: {byte_threshold}バイト）: {path}",
        fix="必要な範囲を選べ。全文が必要な場合は、次の実行例で取得せよ。\n" + codex_acquisition(path),
    )


def _large_multi_read_notice(path_counts: Sequence[tuple[pathlib.Path, _ReadPlan]], byte_threshold: int) -> str:
    if any(_is_session_record(path) for path, _plan in path_counts):
        return _large_warning(
            "原セッション記録を含む複数ファイルの全文取得の出力量を確認せよ。",
            fix="まず取得の必要性と範囲を見直せ。目的に不要なら取得を中止せよ。必要な原記録を"
            "`atk run-script session-review-evidence -- --transcript <原記録の絶対パス>`へ照会条件を渡し、"
            "通常の本文は別の呼び出しで取得せよ。入力の選択は`agent-toolkit:review-session`の`references/session-records.md`に従え。",
            subject="\n".join(str(path) for path, _plan in path_counts),
        )
    total_bytes = sum(plan.byte_count for _path, plan in path_counts)
    details = "、".join(f"`{path}`: {plan.line_count}行、{plan.byte_count}バイト" for path, plan in path_counts)
    return _large_warning(
        f"複数ファイルの全文取得の出力量を確認せよ（合計: {total_bytes}バイト、通知の閾値: {byte_threshold}バイト）: {details}",
        fix="必要な資料と範囲を選べ。全文が必要な資料は、資料ごと・コードブロックごとに別のセルで取得せよ。\n"
        + "\n".join(codex_acquisition(path) for path, _plan in path_counts),
    )


def check_large_bash_read(command: str, cwd: str, *, is_codex: bool = False) -> str | None:
    """規範の見出し抽出と、Codexの閾値を超える全文取得を警告する。"""
    extracted = []
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.segment.tokens
        if (
            not invocation.arguments_known
            or not tokens
            or invocation.captured
            or invocation.output_redirected
            and not invocation.output_pipe
        ):
            continue
        name = pathlib.PurePath(tokens[0]).name
        if name not in {"sed", "awk"} or any(value.startswith(("--in-place", "-i")) for value in tokens[1:]):
            continue
        if not any(re.search(r"(?<![\w\\])/\^\s*#{1,6}", value) for value in tokens[1:]):
            continue
        for operand in _content_operands(tokens, pattern=True):
            if is_dynamic_read_operand(operand) or not pathlib.Path(operand).is_absolute() and not current.resolved:
                continue
            path = _resolve_path(operand, current.path)
            if path.is_file() and read_triggers.is_normative_document(path):
                extracted.append(path.resolve())
    if extracted:
        paths = list(dict.fromkeys(extracted))
        return _large_warning(
            "エージェント向け文書を見出しの正規表現で範囲抽出しようとしている: " + "、".join(map(str, paths)),
            fix="エージェント向け文書は全文を取得せよ。大きい文書は行番号のsedで末尾まで分割取得できる。\n"
            + "\n".join(
                codex_acquisition(path) if is_codex else f"`cat -- {shlex.quote(str(path))}`で全文読め" for path in paths
            ),
        )
    if not is_codex:
        return None
    byte_threshold = _byte_threshold()
    for paths in bash_read_paths(command, cwd):
        sizes: list[tuple[pathlib.Path, int, _ReadPlan | None]] = []
        for path in paths:
            try:
                metadata = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if metadata.st_size > 0:
                sizes.append((path, metadata.st_size, None))
            elif (measurement := _measure_and_plan(path)) is not None:
                # procfsなどのサイズ0の通常ファイルは、内容量をメタデータから確定できない。
                sizes.append((path, measurement.byte_count, measurement))
        if sum(size for _path, size, _plan in sizes) <= byte_threshold:
            continue
        path_counts = []
        for path, _size, cached in sizes:
            measurement = cached or _measure_and_plan(path)
            if measurement is not None:
                path_counts.append((path, measurement))
        if sum(plan.byte_count for _path, plan in path_counts) <= byte_threshold:
            continue
        if len(path_counts) == 1:
            path, plan = path_counts[0]
            return _large_read_notice(path, plan, byte_threshold)
        return _large_multi_read_notice(path_counts, byte_threshold)
    return None
