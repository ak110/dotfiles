"""退避出力の回収と書込先の解析で共有する、シェルのファイル引数を扱う。  pyfltr: allow-comment"""

from __future__ import annotations

import pathlib
lazy import ast
lazy import re
lazy from collections.abc import Iterator, Sequence

lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, BashInvocation, is_unknown_word
lazy from agent_toolkit._common.shell_tokens import ShellWord

_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_SEARCH_COMMANDS = frozenset({"rg", "grep", "egrep", "fgrep"})


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
