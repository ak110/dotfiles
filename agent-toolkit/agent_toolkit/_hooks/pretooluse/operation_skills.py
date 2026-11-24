"""操作の直前に起動を求めるagent-basicsのスキルの表と、その操作の静的な判定と案内。

表（`OPERATION_SKILLS`）は入口`atk-mode`が操作・判断の前に起動を求める4つのスキルに限る。
案内は主体と文脈（会話圧縮・clearで初期化）ごとにスキル1件につき1回の非遮断の通知とし、
案内済みのスキルとClaude Codeの`Skill`で起動済みのスキルについては操作を判定しない。
書込先の解析（`written_paths`）は終了工程の証拠の採取も使う。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re
lazy import tempfile
lazy from collections.abc import Callable, Sequence

lazy from agent_toolkit._atk import managed_temp
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common import shell_segments as _shell_segments
lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, extract_bash_invocations
lazy from agent_toolkit._common.heredocs import heredoc_bodies
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import tool_input as _tool_input
lazy from agent_toolkit._hooks.message_format import llm_notice
lazy from agent_toolkit._hooks.notice import stage_delivery
lazy from agent_toolkit._hooks.pretooluse import shell_checks as _shell_checks
lazy from agent_toolkit._hooks.pretooluse.large_reads import python_file_paths
lazy from agent_toolkit._plan.structure import is_agent_doc_target_file

_SEARCH_COMMANDS = frozenset({"rg", "find"})
_GREP_COMMANDS = frozenset({"grep", "egrep", "fgrep"})
_GREP_RECURSIVE_LONG_OPTIONS = frozenset({"--recursive", "--dereference-recursive"})
_GREP_RECURSIVE_SHORT_OPTIONS = frozenset("rR")
_GREP_SHORT_OPTIONS_WITH_VALUE = frozenset("efmABCDd")
"""値を取る`grep`の短縮オプション。束ねた短縮オプションの中でこの文字より後ろは値として読む。"""
_GREP_LONG_OPTIONS_WITH_VALUE = frozenset(
    {
        "--regexp",
        "--file",
        "--max-count",
        "--after-context",
        "--before-context",
        "--context",
        "--devices",
        "--directories",
        "--label",
        "--include",
        "--exclude",
        "--exclude-from",
        "--exclude-dir",
        "--binary-files",
    }
)


@dataclasses.dataclass(frozen=True)
class OperationSkill:
    skill_name: str
    short_name: str
    operation: str
    matches: Callable[[str, dict, str], bool]
    """ツール名、ツール入力、操作時点のcwdから、操作に当たるかを返す。"""


def _grep_is_recursive(arguments: Sequence[str]) -> bool:
    index = 0
    while index < len(arguments):
        token = arguments[index]
        index += 1
        if token == "--":
            return False
        if token.startswith("--"):
            option, separator, _value = token.partition("=")
            if option in _GREP_RECURSIVE_LONG_OPTIONS:
                return True
            if option in _GREP_LONG_OPTIONS_WITH_VALUE and not separator:
                index += 1
            continue
        if not token.startswith("-") or token == "-":
            continue
        for position, char in enumerate(token[1:], start=1):
            if char in _GREP_RECURSIVE_SHORT_OPTIONS:
                return True
            if char in _GREP_SHORT_OPTIONS_WITH_VALUE:
                if position == len(token) - 1:
                    index += 1
                break
    return False


def _is_search_segment(segment: _shell_segments.ExecutionSegment) -> bool:
    if not segment.resolved or not segment.tokens:
        return False
    name = pathlib.PurePath(segment.tokens[0]).name
    if name in _SEARCH_COMMANDS:
        return True
    if name in _GREP_COMMANDS:
        return _grep_is_recursive(segment.tokens[1:])
    subcommand = _shell_checks.git_subcommand_tokens(segment)
    return subcommand is not None and subcommand[0] == "grep"


def _is_search_operation(tool_name: str, tool_input: dict) -> bool:
    if tool_name in {"Grep", "Glob"}:
        return True
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return any(_is_search_segment(pipeline[0]) for pipeline in _shell_segments.extract_execution_pipelines(command))


_ROOT_CAUSE_HEADING = re.compile(r"^## 原因分析[ \t]*$", re.MULTILINE)
"""原因分析の見出しだけから成る行。AWIと計画ファイルの`## 原因分析`の起草で書く。"""


def _is_root_cause_writing(tool_name: str, tool_input: dict) -> bool:
    """`agent-basics:bugfix`の起動の契機とする原因分析の記述かを返す。

    編集ツールは変更後の断片（Claude Codeの`Write`・`Edit`・`MultiEdit`とCodexの`apply_patch`）、
    Bashはコマンド文字列に、`## 原因分析`だけから成る行がある場合を対象とする。
    """
    if tool_name == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and _ROOT_CAUSE_HEADING.search(command) is not None
    fields = _tool_input.new_content_fields(tool_name, tool_input)
    return fields is not None and any(_ROOT_CAUSE_HEADING.search(value) for _, value in fields)


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


def _static_write_path(path: str, *, partial: bool = False) -> str | None:
    """静的に確定した書込先を返す。未知の展開を含むパスは実パスとして確定しないため`None`を返す。

    `partial`が真なら、文書種別の識別など末尾だけを使う用途へ、最後の展開より後ろが`/`で始まる末尾を返す。
    `$ROOT/path.md`の末尾`/path.md`は実在の場所を表さないため、実パスを求める用途では`partial`を使わない。
    """
    if STATIC_UNKNOWN not in path:
        return path
    if not partial:
        return None
    suffix = path.rsplit(STATIC_UNKNOWN, 1)[1]
    return suffix if suffix.startswith("/") else None


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


def _bash_write_paths(command: str, cwd: str | None = None, *, partial: bool = False) -> list[str]:
    """リダイレクト・tee・sponge・gawk・sed・cp・mv・install・rsync・perl・dd・Pythonの書込先を返す。

    リダイレクト先は実行するコマンドの確定と独立して構文から決まるため、未知の実行
    （`$COMMAND > out`、リダイレクトだけの文など）の静的な出力先も返す。コマンド名に依存する書込先は、
    実行するコマンドを確定できた呼出しだけから求める。本文と環境変数を実行して解決しない。
    未知の展開を含むパスは返さない。`partial`が真なら、文書種別の識別に使う静的な末尾も返す。
    gawkのinplaceとspongeも内容の書込先として扱う。touch・ln・mkdir・truncateは本文の編集とは限らず、
    書込先の指定にも別の解釈を要するため対象へ含めない。
    """
    paths: list[str] = []

    def append_paths(found: list[str], base: shell_cwd.CwdResolution) -> None:
        for raw in found:
            value = _static_write_path(raw, partial=partial)
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


def _is_agent_document_writing(tool_name: str, tool_input: dict, cwd: str = "") -> bool:
    if tool_name == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and any(
            is_agent_doc_target_file(path) for path in _bash_write_paths(command, cwd or None, partial=True)
        )
    operations = _tool_input.parse_operations(tool_name, tool_input, cwd)
    return operations is not None and any(
        is_agent_doc_target_file(path) and not _is_discard_path(pathlib.Path(path))
        for operation in operations
        for path in operation.display_paths
    )


def is_body_writing(tool_name: str, tool_input: dict, cwd: str = "") -> bool:
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return False
        handoff = _delegated_session.handoff_path(os.environ)
        paths = _bash_write_paths(command, cwd or None)
        if handoff and paths and all(path == handoff for path in paths):
            return False
        return _is_agent_document_writing(tool_name, tool_input, cwd) or _bash_body_writing(command, cwd)
    operations = _tool_input.parse_operations(tool_name, tool_input, cwd)
    handoff = _delegated_session.handoff_path(os.environ)
    paths = [path for operation in operations or [] for path in operation.display_paths]
    if handoff and paths and all(path == handoff for path in paths):
        return False
    return any(
        operation.path
        and not _is_discard_path(pathlib.Path(operation.path))
        and operation.kind != _tool_input.KIND_DELETE
        and (
            bool(operation.whole_after_text)
            if operation.is_whole_write
            else any(fragment.before != fragment.after for fragment in operation.fragments)
        )
        for operation in operations or []
    )


def _is_temporary_path(path: str) -> bool:
    """成果物ではない一時領域（managed-tempの一時rootと、Claude Codeの一時領域）の配下かを返す。

    Claude Codeはタスク出力とscratchpadを`<一時ディレクトリ>/claude-<uid>/`の下へ置く。
    一時ディレクトリ全体は作業ツリーの検証用複製なども置かれ得るため含めない。
    """
    candidate = pathlib.Path(path).resolve()
    roots = [managed_temp.temp_root_path()]
    if hasattr(os, "getuid"):
        roots.append(pathlib.Path(tempfile.gettempdir()) / f"claude-{os.getuid()}")
    return any(candidate.is_relative_to(root.resolve()) for root in roots)


def _is_artifact_body_writing(tool_name: str, tool_input: dict, cwd: str = "") -> bool:
    written = written_paths(tool_name, tool_input, cwd)
    if written and all(_is_temporary_path(path) for path in written):
        return False
    return is_body_writing(tool_name, tool_input, cwd)


def _bash_body_writing(command: str, cwd: str = "") -> bool:
    for invocation, base in shell_cwd.bash_invocation_cwds(command, cwd):
        tokens = invocation.static_tokens
        name = pathlib.PurePath(tokens[0]).name
        outputs: list[str] = []
        if name == "sed":
            outputs = _sed_write_paths(tokens[1:])
        elif name == "perl":
            outputs = _perl_write_paths(tokens[1:])
        elif name == "gawk":
            outputs = _gawk_write_paths(tokens[1:])
        elif is_python_token(name) and len(tokens) > 2 and tokens[1] == "-c":
            outputs = _python_write_paths(tokens[2])
        if name in {"cat", "tee"} and invocation.inline_input:
            outputs = [target for target in invocation.static_outputs if isinstance(target, str)]
            if name == "tee":
                outputs.extend(_stream_write_paths(tokens[1:]))
        for raw in outputs:
            value = _static_write_path(raw, partial=True)
            if value is None or value == "-":
                continue
            path = pathlib.Path(value).expanduser()
            if not path.is_absolute() and base.resolved:
                path = pathlib.Path(base.path) / path
            if not _is_discard_path(path):
                return True
    for body in heredoc_bodies(command):
        preceding = list(shell_cwd.bash_invocation_cwds(command[: body.start], cwd))
        if preceding:
            invocation, base = preceding[-1]
            tokens = invocation.segment.tokens
            if (
                is_python_token(pathlib.PurePath(tokens[0]).name)
                and tokens[1:] in {(), ("-",)}
                and any(
                    not _is_discard_path(
                        pathlib.Path(base.path) / pathlib.Path(path).expanduser()
                        if base.resolved
                        else pathlib.Path(path).expanduser()
                    )
                    for path in _python_write_paths(command[body.start : body.end])
                )
            ):
                return True
    return False


_VERIFICATION_MCP_TOOL_NAMES = frozenset({"mcp__plugin_agent-toolkit_pyfltr__run", "mcp__pyfltr__run"})
"""検証を実行するpyfltrのMCPツール。Claude Codeの配布プラグインと、Codex・委譲先の`pyfltr`サーバーの綴りを含む。"""

_PYFLTR_RUN_SUBCOMMANDS = frozenset({"run", "fast", "ci", "run-for-agent"})
_TASK_TARGETS = frozenset({"test", "check", "lint", "format", "verify"})
_QUERY_OPTIONS = frozenset({"--help", "-h", "--version", "--collect-only", "--co"})
"""検証を実行せずに照会だけを行うオプション。"""
_UV_VALUE_OPTIONS = frozenset(
    {"--with", "--with-editable", "--with-requirements", "--python", "-p", "--project", "--directory", "--package"}
    | {"--extra", "--group", "--env-file", "--from", "--index", "-w"}
)
"""`uv run`・`uvx`の値を取るオプション。実行するコマンドの位置を求めるときは、直後の値もオプションの一部として除く。"""


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


OPERATION_SKILLS: tuple[OperationSkill, ...] = (
    OperationSkill("agent-basics:search", "search", "検索", lambda name, data, _cwd: _is_search_operation(name, data)),
    OperationSkill(
        "agent-basics:bugfix", "bugfix", "原因分析の記述", lambda name, data, _cwd: _is_root_cause_writing(name, data)
    ),
    OperationSkill("agent-basics:writing-standards", "writing-standards", "本文の作成・更新", _is_artifact_body_writing),
    OperationSkill(
        "agent-basics:check-execution",
        "check-execution",
        "検証の実行",
        lambda name, data, _cwd: is_verification_operation(name, data),
    ),
)
"""操作の直前に起動を求めるスキルの表。案内の文面と対象はこの表だけから作る。"""

NOTIFIED_KEY = "operation_skills_notified"
_OBSERVED_KEY = "operation_skills_observed"
SELF_CHECKED_KEY = "operation_skills_self_checked"


def canonical_skill_name(skill_name: object) -> str | None:
    return next(
        (entry.skill_name for entry in OPERATION_SKILLS if skill_name in {entry.skill_name, entry.short_name}),
        None,
    )


def notices(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> list[str]:
    if not session_id or _delegated_session.is_lightweight_delegated(os.environ):
        return []
    subject = _agent_id.resolve_hook_agent_id(payload)
    state = read_state(session_id)
    delivered, observed = state.get(NOTIFIED_KEY), state.get(_OBSERVED_KEY)
    advised = delivered.get(subject, []) if isinstance(delivered, dict) else []
    done = observed.get(subject, []) if isinstance(observed, dict) else []
    cwd = str(payload.get("cwd", ""))
    texts = []
    for entry in OPERATION_SKILLS:
        if entry.skill_name in done or entry.skill_name in advised or not entry.matches(tool_name, tool_input, cwd):
            continue
        text = llm_notice(
            f"{entry.operation}の操作を検出した。スキル`{entry.skill_name}`をまだ起動していなければ、起動して手順に従ってください。",
            "pretooluse",
        )
        stage_delivery(text, session_id, NOTIFIED_KEY, subject, entry.skill_name)
        texts.append(text)
    return texts


def unconfirmed_skills(session_id: str, subject: str) -> list[str]:
    """案内したのに起動を確認できず、点検でまだ確認を促していないスキル名を返す。  pyfltr: allow-comment

    促し終えた記録は、点検の通知の配送時に`SELF_CHECKED_KEY`へ入る（通知が採用されなければ次の点検で再び返す）。
    """
    if not session_id:
        return []
    state = read_state(session_id)
    delivered, observed, checked = state.get(NOTIFIED_KEY), state.get(_OBSERVED_KEY), state.get(SELF_CHECKED_KEY)
    advised = delivered.get(subject, []) if isinstance(delivered, dict) else []
    done = observed.get(subject, []) if isinstance(observed, dict) else []
    seen = checked.get(subject, []) if isinstance(checked, dict) else []
    return [name for name in advised if isinstance(name, str) and name not in done and name not in seen]


def observe_skill(session_id: str, subject: str, skill_name: object) -> None:
    """Claude Codeの`Skill`で起動した表のスキルを、その主体へ以後案内しないよう記録する。"""
    name = canonical_skill_name(skill_name)
    if name is None or not session_id:
        return

    def record(state: dict) -> dict | None:
        records = state.get(_OBSERVED_KEY)
        records = dict(records) if isinstance(records, dict) else {}
        values = records.get(subject)
        values = list(values) if isinstance(values, list) else []
        if name in values:
            return None
        records[subject] = [*values, name]
        state[_OBSERVED_KEY] = records
        return state

    update_state(session_id, record)


def forget_subject(session_id: str, subject: str) -> None:

    def forget(state: dict) -> dict | None:
        updates = {}
        for key in (NOTIFIED_KEY, _OBSERVED_KEY, SELF_CHECKED_KEY):
            records = state.get(key)
            if isinstance(records, dict) and subject in records:
                updates[key] = {name: value for name, value in records.items() if name != subject}
        return {**state, **updates} if updates else None

    update_state(session_id, forget)
