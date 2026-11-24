"""原記録の直接参照と規範の見出し抽出、出力の上限を超える複数ファイルの取得へ、適切な取得方法を案内する。"""

from __future__ import annotations

import pathlib
lazy import ast
lazy import re
lazy from collections.abc import Iterator, Sequence

from agent_toolkit._hooks.notice import formatter
lazy from agent_toolkit._common import host_homes, shell_cwd
lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, BashInvocation, is_unknown_word
lazy from agent_toolkit._common.codex_execution_inputs import command_inputs
lazy from agent_toolkit._common.shell_tokens import ShellWord
lazy from agent_toolkit._common.uv_arguments import is_python_token

_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_SEARCH_COMMANDS = frozenset({"rg", "grep", "egrep", "fgrep"})
_notice = formatter("pretooluse")


def _large_warning(body: str, *, fix: str, subject: str = "") -> str:
    return _notice(body, tag="warn", fix=fix, removable_cause=False, subject=subject)


def _resolve_path(value: str, cwd: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return path if path.is_absolute() else pathlib.Path(cwd) / path


def _display_operands(name: str, tokens: Sequence[str]) -> tuple[str, ...]:
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
    if not tokens:
        return False
    name = pathlib.PurePath(tokens[0]).name
    if name in _SEARCH_COMMANDS:
        return not any(token == "--pre" or token.startswith(("--pre=", "--pre-glob")) for token in tokens[1:])
    if name in {"head", "tail"}:
        return True
    return bool(_read_operands(tokens, include_partial=True))


def sed_read_range(tokens: Sequence[str]) -> tuple[str, int, int | None] | None:
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

    return operand, start, max(start, int(matched[2]))


def is_dynamic_read_operand(operand: str) -> bool:
    unquoted = operand.unquoted if isinstance(operand, ShellWord) else operand
    return (
        is_unknown_word(operand)
        or any(marker in unquoted for marker in ("$", "`", "<", ">", "*", "?", "["))
        or re.search(r"\{[^{}]*,[^{}]*\}|\{(?:-?\d+\.\.-?\d+|[a-zA-Z]\.\.[a-zA-Z])(?:\.\.-?\d+)?\}", unquoted) is not None
    )


def bash_read_paths(
    command: str, cwd: str, *, include_partial: bool = False, first_line_only: bool = False, include_piped: bool = False
) -> Iterator[tuple[pathlib.Path, ...]]:
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd, effects=True):
        paths = invocation_read_paths(
            invocation, current, include_partial=include_partial, first_line_only=first_line_only, include_piped=include_piped
        )
        if paths:
            yield paths


def invocation_read_paths(
    invocation: BashInvocation,
    current: shell_cwd.CwdResolution,
    *,
    include_partial: bool,
    first_line_only: bool,
    include_piped: bool = False,
) -> tuple[pathlib.Path, ...]:
    if not invocation.execution_known or invocation.captured:
        return ()
    if invocation.output_redirected and not (include_piped and invocation.output_pipe):
        return ()
    tokens = invocation.segment.tokens
    if not invocation.arguments_known:
        tokens = tuple(
            ShellWord(resolved, unquoted=word.unquoted)
            if isinstance(word, ShellWord)
            and is_unknown_word(word)
            and STATIC_UNKNOWN not in resolved
            and not resolved.startswith("~")
            else word
            for word, resolved in zip(tokens, invocation.variable_tokens, strict=True)
        )
        if not include_partial and any(is_dynamic_read_operand(token) for token in tokens):
            return ()
    return tuple(
        _resolve_path(operand, current.path)
        for operand in _read_operands(tokens, include_partial=include_partial, first_line_only=first_line_only)
        if not is_dynamic_read_operand(operand) and (current.resolved or pathlib.Path(operand).expanduser().is_absolute())
    )


def _is_session_record(path: pathlib.Path, *, directory: bool = False) -> bool:
    resolved = path.resolve()
    roots = (host_homes.claude_config_dir() / "projects", host_homes.codex_home() / "sessions")
    return any(resolved.is_relative_to(root.resolve()) for root in roots) and (
        resolved.suffix == ".jsonl" or directory and (not resolved.suffix or resolved.is_dir())
    )


def _content_operands(tokens: Sequence[str], *, pattern: bool) -> tuple[str, ...]:
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


def _search_metadata_only(tokens: Sequence[str]) -> bool:
    name = pathlib.PurePath(tokens[0]).name

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
    found: list[pathlib.Path] = []
    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.segment.tokens

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
    paths: list[pathlib.Path] = []
    if tool_name in {"Read", "Grep"}:
        value = tool_input.get("file_path" if tool_name == "Read" else "path", cwd if tool_name == "Grep" else "")
        if isinstance(value, str) and value:
            path = _resolve_path(value, cwd)
            if _is_session_record(path, directory=tool_name == "Grep"):
                paths.append(path)
    elif tool_name == "Bash" and isinstance(tool_input.get("command"), str):
        paths = _bash_session_record_paths(tool_input["command"], cwd)
    elif tool_name in {"exec_command", "functions.exec_command"} and isinstance(command := tool_input.get("cmd"), str):
        workdir = tool_input.get("workdir")
        paths = _bash_session_record_paths(command, workdir if isinstance(workdir, str) else cwd)
    elif tool_name == "functions.exec" and isinstance(code := tool_input.get("input"), str):
        for command in command_inputs(tool_name, code):
            # 外側セルのcwdを、別のworkdirを指定できる内側コマンドのcwdとはみなさない。  # pyfltr: allow-comment
            paths.extend(_bash_session_record_paths(command, ""))
    if not paths:
        return None
    return _notice(
        "セッション原記録の直接参照である。\n対象: " + "、".join(str(path) for path in dict.fromkeys(paths)),
        tag="warn",
        fix=(
            "`atk run-script session-review-evidence -- --transcript <原記録の絶対パス>`で本文を照会してください。"
            "今回必要な照会条件は`agent-toolkit:atk-mode/references/session-records.md`「本文の検索」で選んでください。"
            "失敗文言の横断照会は`--tool-calls --failure-fixed-string <文言>`と同資料の横断走査の指定を使ってください。"
            "人の入力を横断で数える時は同資料の`--catalog-*`と`--user-events`を併せて使ってください。"
        ),
        removable_cause=False,
        escalate_on_repeat=False,
    )


def _is_normative_document(path: pathlib.Path) -> bool:
    path = path.resolve()
    if path.name in {"AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "GEMINI.md"}:
        return True
    # ホスト側のリンクをresolveするとchezmoiの原本へ戻るため、配布元も同じ範囲で識別する。
    if path.name.endswith((".md", ".md.tmpl")):
        for parent in path.parents:
            if parent.name not in {"skills", "rules"}:
                continue
            host = parent.parent
            if host.name == "antigravity-cli" and host.parent.name == "dot_gemini":
                host = host.parent
            if host.name in {"dot_claude", "dot_codex", "dot_agents", "dot_gemini"} and host.parent.name == ".chezmoi-source":
                return True
    if path.suffix != ".md":
        return False
    if any(
        parent.name in {"skills", "rules"} and parent.parent.name in {".claude", ".codex", ".agents", ".gemini"}
        for parent in path.parents
    ):
        return True
    owner = next((parent for parent in path.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
    return owner is not None and path.relative_to(owner).parts[0] == "skills"


def check_large_bash_read(command: str, cwd: str) -> str | None:
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
            if path.is_file() and _is_normative_document(path):
                extracted.append(path.resolve())
    if extracted:
        paths = list(dict.fromkeys(extracted))
        return _large_warning(
            "エージェント向け文書を見出しの正規表現で範囲抽出しようとしている: " + "、".join(map(str, paths)),
            fix="全文を取得してください。",
        )
    return None


CLAUDE_BASH_OUTPUT_CHARS = 30_000
"""`BASH_MAX_OUTPUT_LENGTH`を設定しない場合のClaude CodeのBash出力の上限（文字）。"""
CODEX_OUTPUT_TOKENS = 20_000
"""Codexの資料取得で両層の`max_output_tokens`に明示する値（`atk-mode`の`playbooks/host-codex.md`「Codex固有の入出力」）。"""
_CODEX_ENVELOPE_TOKENS = 200
"""Codexの外側`functions.exec`のJSON包絡の見積り（トークン）。本文量が同じでも外側だけが超える場合を含める。"""


def check_bundled_read(
    command: str,
    cwd: str,
    *,
    codex: bool,
    environment: dict[str, str],
    max_output_tokens: object = None,
) -> str | None:
    paths = display_read_paths(command, cwd)
    if len(paths) < 2:
        return None

    full = {path.resolve() for group in bash_read_paths(command, cwd) for path in group}
    remaining = [path for path in paths if path in full]
    if len(remaining) < 2:
        return None
    sizes = {path: path.stat().st_size for path in remaining}
    if codex:
        budget = max_output_tokens if isinstance(max_output_tokens, int) and max_output_tokens > 0 else CODEX_OUTPUT_TOKENS
        # 文章主体の見積りはUTF-8バイト数÷3の切上げとする（同上の規約）。
        estimate = sum(-(-size // 3) for size in sizes.values()) + _CODEX_ENVELOPE_TOKENS
        unit = "トークン"
    else:
        value = environment.get("BASH_MAX_OUTPUT_LENGTH", "")
        budget = int(value) if value.isdigit() and int(value) > 0 else CLAUDE_BASH_OUTPUT_CHARS
        estimate = 0
        for path in remaining:
            try:
                estimate += len(path.read_bytes().decode("utf-8", errors="replace"))
            except OSError:
                estimate += sizes[path]
        unit = "文字"
    if estimate <= budget:
        return None
    return _large_warning(
        f"複数ファイルの合計の見積り{estimate}{unit}が出力の上限{budget}{unit}を超え、切り詰められる。",
        fix="上限に収まる単位へファイルを分けて取得し、切り詰められた本文は未取得として補ってください。",
    )
