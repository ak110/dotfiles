"""書込先と検証操作を解析し、成果作業と実際の失敗の観測に使う。  pyfltr: allow-comment"""

from __future__ import annotations

import os
import pathlib
import re
lazy from collections.abc import Sequence

lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, extract_bash_invocations
lazy from agent_toolkit._common.heredocs import heredoc_bodies
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks import tool_input as _tool_input
lazy from agent_toolkit._hooks.pretooluse.large_reads import python_file_paths


def _python_write_paths(code: str) -> list[str]:
    return python_file_paths(code, writing=True)


def _sed_write_paths(arguments: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    has_script = False
    inplace = False
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg in {"--help", "--version"}:
            return []
        if arg in {"-e", "--expression", "-f", "--file"}:
            has_script = True
            index += 2
            continue
        if arg.startswith(("-e", "-f", "--expression=", "--file=")):
            has_script = True
        elif arg == "--in-place" or arg.startswith(("-i", "--in-place=")):
            inplace = True
        elif arg.startswith("-") and not arg.startswith("--"):
            for position, char in enumerate(arg[1:], 1):
                if char == "i":
                    inplace = True
                    break
                if char in "ef":
                    has_script = True
                    if position == len(arg) - 1:
                        index += 1
                    break
        elif arg == "--":
            paths.extend(arguments[index + 1 :])
            break
        elif not arg.startswith("-"):
            paths.append(arg)
        index += 1
    return (paths if has_script else paths[1:]) if inplace else []


def _copy_write_paths(name: str, arguments: tuple[str, ...]) -> list[str]:
    operands: list[str] = []
    directory: str | None = None
    no_directory = False
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        index += 1
        if arg == "--":
            operands.extend(arguments[index:])
            break
        if arg in {"--help", "--version"} or name == "install" and arg == "--directory":
            return []
        if arg.startswith("--target-directory="):
            directory = arg.split("=", 1)[1]
        elif arg in {"--target-directory", "-t"}:
            if index < len(arguments):
                directory = arguments[index]
            index += 1
        elif arg in {"-T", "--no-target-directory"}:
            no_directory = True
        elif arg.startswith("-") and not arg.startswith("--"):
            valued = "gmoSt" if name == "install" else "St" if name in {"cp", "mv"} else "e"
            for position, char in enumerate(arg[1:], 1):
                if char == "T" and name in {"cp", "mv", "install"}:
                    no_directory = True
                if char == "d" and name == "install":
                    return []
                if char in valued:
                    value = arg[position + 1 :] or (arguments[index] if index < len(arguments) else "")
                    if not arg[position + 1 :]:
                        index += 1
                    if char == "t":
                        directory = value
                    break
        elif arg in {"--suffix", "--group", "--mode", "--owner", "--rsh", "--exclude", "--include"}:
            index += 1
        elif not arg.startswith("-"):
            operands.append(arg)
    if directory is None:
        if len(operands) < 2:
            return []
        *sources, destination = operands
        if no_directory or len(sources) == 1 and not destination.endswith("/"):
            return [destination]
        directory = destination
    else:
        sources = operands
    return [directory.rstrip("/") + "/" + source.rstrip("/").rsplit("/", 1)[-1] for source in sources]


def _perl_write_paths(arguments: tuple[str, ...]) -> list[str]:
    inplace = False
    inline = False
    operands: list[str] = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        index += 1
        if arg == "--" or arg == "-" or not arg.startswith("-"):
            if arg != "--":
                operands.append(arg)
            operands.extend(arguments[index:])
            break
        if arg.startswith("--"):
            return []
        position = 1
        while position < len(arg):
            char = arg[position]
            position += 1
            if char == "i":
                inplace = True
                break
            if char in "eEmMI":
                inline = inline or char in "eE"
                if position == len(arg):
                    index += 1
                break
            if char in "CDFx":
                break
            if char in "l0":
                number = re.match(
                    r"x[0-9a-fA-F]+" if char == "0" and arg[position:].startswith("x") else r"[0-7]*", arg[position:]
                )
                if number is not None:
                    position += len(number[0])
            elif char == "d":
                if arg[position:].startswith("t"):
                    position += 1
                if arg[position:].startswith(":"):
                    break
            elif char in "ch?vV" or char not in "afnpsSTtuUwWX":
                return []
    return (operands if inline else operands[1:]) if inplace else []


def _static_write_path(path: str) -> str | None:
    return path if STATIC_UNKNOWN not in path else None


def _gawk_write_paths(arguments: tuple[str, ...]) -> list[str]:
    inplace = False
    has_program = False
    operands: list[str] = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        index += 1
        if arg == "--":
            operands.extend(arguments[index:])
            break
        if arg in {"--help", "--version", "-h", "-V"}:
            return []
        if arg in {"-i", "--include", "-f", "--file", "-e", "--source", "-v", "--assign", "-F", "--field-separator"}:
            value = arguments[index] if index < len(arguments) else ""
            index += 1
            inplace |= arg in {"-i", "--include"} and value == "inplace"
            has_program |= arg in {"-f", "--file", "-e", "--source"}
        elif arg.startswith(("--include=", "-i")):
            inplace |= (arg.split("=", 1)[1] if arg.startswith("--") else arg[2:]) == "inplace"
        elif arg.startswith(("--file=", "--source=", "-f", "-e")):
            has_program = True
        elif arg.startswith(("--assign=", "--field-separator=", "-v", "-F")):
            continue
        elif not arg.startswith("-"):
            operands.append(arg)
    files = operands if has_program else operands[1:]
    return [path for path in files if path != "-" and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", path)] if inplace else []


def _stream_write_paths(arguments: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for index, arg in enumerate(arguments):
        if arg == "--":
            paths.extend(arguments[index + 1 :])
            break
        if arg in {"--help", "--version"}:
            return []
        if not arg.startswith("-"):
            paths.append(arg)
    return paths


def _is_discard_path(path: pathlib.Path) -> bool:
    try:
        return path.samefile(os.devnull)
    except OSError:
        return False


def _bash_write_paths(command: str, cwd: str | None = None) -> list[str]:
    """リダイレクト・tee・sponge・gawk・sed・cp・mv・install・rsync・perl・dd・Pythonの書込先を返す。  pyfltr: allow-comment

    リダイレクト先は実行するコマンドの確定と独立して構文から決まるため、未知の実行
    （`$COMMAND > out`、リダイレクトだけの文など）の静的な出力先も返す。コマンド名に依存する書込先は、
    実行するコマンドを確定できた呼出しだけから求める。本文と環境変数を実行して解決しない。
    未知の展開を含むパスは返さない。
    gawkのinplaceとspongeも内容の書込先として扱う。touch・ln・mkdir・truncateは本文の編集とは限らず、
    書込先の指定にも別の解釈を要するため対象へ含めない。
    """
    paths: list[str] = []

    def append_paths(found: list[str], base: shell_cwd.CwdResolution) -> None:
        for raw in found:
            value = _static_write_path(raw)
            if value is None:
                continue
            path = pathlib.Path(value).expanduser()
            if path.is_absolute():
                paths.append(str(path.resolve()))
            elif base.resolved:
                paths.append(str((pathlib.Path(base.path) / path).resolve()))
            elif cwd is None and base.unresolved_expression is None:
                paths.append(value)

    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd or "", effects=True):
        found = [target for target in invocation.variable_outputs if isinstance(target, str)]
        tokens = invocation.variable_tokens
        if not invocation.execution_known or not tokens:
            append_paths(found, current)
            continue
        name = pathlib.PurePath(tokens[0]).name
        if name in {"tee", "sponge"}:
            found.extend(path for path in _stream_write_paths(tokens[1:]) if name != "tee" or path != "-")
        elif name == "gawk":
            found.extend(_gawk_write_paths(tokens[1:]))
        elif name == "sed":
            found.extend(_sed_write_paths(tokens[1:]))
        elif name in {"cp", "mv", "install", "rsync"}:
            found.extend(_copy_write_paths(name, tokens[1:]))
        elif name == "perl":
            found.extend(_perl_write_paths(tokens[1:]))
        elif name == "dd":
            found.extend(arg[3:] for arg in tokens[1:] if arg.startswith("of="))
        elif is_python_token(name) and len(tokens) > 2 and tokens[1] == "-c":
            found.extend(_python_write_paths(tokens[2]))
        append_paths(found, current)
    for body in heredoc_bodies(command):
        invocations = list(shell_cwd.bash_invocation_cwds(command[: body.start], cwd or ""))
        if invocations:
            invocation, base = invocations[-1]
            tokens = invocation.segment.tokens
            if is_python_token(pathlib.PurePath(tokens[0]).name) and tokens[1:] in {(), ("-",)}:
                append_paths(_python_write_paths(command[body.start : body.end]), base)
    return [path for path in paths if not _is_discard_path(pathlib.Path(path))]


def written_paths(tool_name: str, tool_input: dict, cwd: str) -> list[str]:
    if tool_name == "Bash":
        command = tool_input.get("command")
        return _bash_write_paths(command, cwd) if isinstance(command, str) else []
    operations = _tool_input.parse_operations(tool_name, tool_input, cwd)
    return [
        str(pathlib.Path(operation.path).resolve())
        for operation in operations or []
        if operation.path and not _is_discard_path(pathlib.Path(operation.path))
    ]


_VERIFICATION_MCP_TOOL_NAMES = frozenset({"mcp__plugin_agent-toolkit_pyfltr__run", "mcp__pyfltr__run"})


_PYFLTR_RUN_SUBCOMMANDS = frozenset({"run", "fast", "ci", "run-for-agent"})


_TASK_TARGETS = frozenset({"test", "check", "lint", "format", "verify"})


_QUERY_OPTIONS = frozenset({"--help", "-h", "--version", "--collect-only", "--co"})


_UV_VALUE_OPTIONS = frozenset(
    {"--with", "--with-editable", "--with-requirements", "--python", "-p", "--project", "--directory", "--package"}
    | {"--extra", "--group", "--env-file", "--from", "--index", "-w"}
)


def _skip_runner_options(tokens: Sequence[str]) -> Sequence[str]:
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 2 if tokens[index] in _UV_VALUE_OPTIONS else 1
    return tokens[index:]


def _is_task_target(target: str) -> bool:
    return target in _TASK_TARGETS or target.startswith(("test-", "test:"))


def _is_verification_tokens(tokens: Sequence[str]) -> bool:
    if not tokens or _QUERY_OPTIONS.intersection(tokens):
        return False
    name = pathlib.PurePath(tokens[0]).name
    rest = tokens[1:]
    if name == "uv" and tuple(rest[:1]) == ("run",):
        return _is_verification_tokens(_skip_runner_options(rest[1:]))
    if name in {"uvx", "npx", "pnpx"}:
        return _is_verification_tokens(_skip_runner_options(rest))
    if is_python_token(name) and tuple(rest[:1]) == ("-m",):
        return _is_verification_tokens(rest[1:])
    if name in {"npm", "pnpm", "yarn"}:
        if tuple(rest[:1]) == ("exec",):
            return _is_verification_tokens(rest[1:])
        targets = rest[1:] if tuple(rest[:1]) == ("run",) else rest
        return bool(targets) and _is_task_target(targets[0])
    if name == "make" or name == "mise" and tuple(rest[:1]) == ("run",):
        targets = rest[1:] if name == "mise" else rest
        return any(_is_task_target(target) for target in targets if not target.startswith("-") and "=" not in target)
    if name == "pyfltr":
        return bool(rest) and rest[0] in _PYFLTR_RUN_SUBCOMMANDS
    if name in {"pytest", "py.test", "vitest"}:
        return True
    return name in {"playwright", "cargo", "dotnet"} and tuple(rest[:1]) == ("test",)


def is_verification_operation(tool_name: str, tool_input: dict) -> bool:
    if tool_name in _VERIFICATION_MCP_TOOL_NAMES:
        return True
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return any(_is_verification_tokens(invocation.segment.tokens) for invocation in extract_bash_invocations(command))
