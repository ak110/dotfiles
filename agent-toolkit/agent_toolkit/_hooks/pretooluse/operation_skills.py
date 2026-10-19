"""操作の直前に、必要なスキルの取得をホスト本来の手段で求める。

Skill起動も読取も観測されていない主体の最初の操作を止め、必須資料の絶対パスと取得手段、同じ操作の再発行を案内する。
Claude Codeはツール`Skill`での起動、Codexと`Skill`を使えないサブエージェントは`SKILL.md`の読取を取得手段とする。
遮断の応答へ本文を載せない。Skill起動が持つ圧縮後の復元や`${CLAUDE_PLUGIN_ROOT}`の展開を代替できないためである。
取得はPostToolUse（`posttooluse.py`）が観測して記録し、遮断の時点では記録しない。
agents_serverの軽量委譲先（explore・shell・write）はスキルを使わない前提で起動するため遮断しない。

主体は`agent_id.resolve_hook_agent_id`で区別する。agents_serverの委譲先は独立した会話の状態を使う。
表へスキルを加える場合は、操作の判定関数と正例・負例のテストを同時に加える。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import re

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from collections.abc import Callable, Sequence

from agent_toolkit._hooks.pretooluse.notices import _block_notice
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common import shell_segments as _shell_segments
lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, extract_bash_invocations
lazy from agent_toolkit._common.heredocs import heredoc_bodies
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.shell_tokens import is_atk_command
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import document_reads as _document_reads
lazy from agent_toolkit._hooks import plugin_resources as _plugin_resources
lazy from agent_toolkit._hooks import rules_context as _rules_context
lazy from agent_toolkit._hooks import tool_input as _tool_input
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse import shell_checks as _shell_checks
lazy from agent_toolkit._hooks.pretooluse.agent_checks import AGENTS_SERVER_HOOK_TOOL_NAMES
lazy from agent_toolkit._hooks.pretooluse.large_reads import python_file_paths
lazy from agent_toolkit._hooks.pretooluse.task_document_launch import AGENT_TOOL_NAMES
lazy from agent_toolkit._plan.structure import is_agent_doc_target_file

_SEARCH_COMMANDS = frozenset({"rg", "find"})
_GREP_COMMANDS = frozenset({"grep", "egrep", "fgrep"})
_GREP_RECURSIVE_LONG_OPTIONS = frozenset({"--recursive", "--dereference-recursive"})
_GREP_RECURSIVE_SHORT_OPTIONS = frozenset("rR")
_GREP_SHORT_OPTIONS_WITH_VALUE = frozenset("efmABCDd")
"""値を取る`grep`の短縮オプション。束ねた短縮オプションの中でこの文字より後ろは値として読む。"""


SKILL_DOCUMENT = "SKILL.md"

SUBJECT_LIGHTWEIGHT = "lightweight"
SUBJECT_CODEX = "codex"
SUBJECT_SUBAGENT = "subagent"
SUBJECT_MAIN = "main"
"""主体の区分。メインはClaude Codeのメイン会話とagents_serverのtask・delegateのClaude委譲先を含む。"""


@dataclasses.dataclass(frozen=True)
class OperationSkill:
    """操作を起動の契機とするスキルと、その操作の判定関数、必須の参照資料の組。"""

    skill_name: str
    short_name: str
    operation: str
    matches: Callable[[str, dict], bool]
    references: tuple[str, ...] = ()
    """`SKILL.md`に加えて取得を求める、スキルのディレクトリからの相対パス。"""

    @property
    def documents(self) -> tuple[str, ...]:
        """取得を求める全資料の相対パス。"""
        return (SKILL_DOCUMENT, *self.references)


def _grep_is_recursive(arguments: Sequence[str]) -> bool:
    """`grep`系の引数列が再帰オプションを持つかを返す。"""
    index = 0
    while index < len(arguments):
        token = arguments[index]
        index += 1
        if token == "--":
            return False
        if token.startswith("--"):
            if token.split("=", 1)[0] in _GREP_RECURSIVE_LONG_OPTIONS:
                return True
            continue
        if not token.startswith("-") or token == "-":
            continue
        for position, char in enumerate(token[1:], start=1):
            if char in _GREP_RECURSIVE_SHORT_OPTIONS:
                return True
            if char in _GREP_SHORT_OPTIONS_WITH_VALUE:
                # 値を同じトークンへ連結しない場合は次のトークンが値になる。
                if position == len(token) - 1:
                    index += 1
                break
    return False


def _is_search_segment(segment: _shell_segments.ExecutionSegment) -> bool:
    """パイプラインの先頭区間が、リポジトリまたはディレクトリを検索するコマンドかを返す。"""
    if not segment.resolved or not segment.tokens:
        return False
    name = pathlib.PurePath(segment.tokens[0]).name
    if name in _SEARCH_COMMANDS:
        return True
    if name in _GREP_COMMANDS:
        return _grep_is_recursive(segment.tokens[1:])
    subcommand = _shell_checks._git_subcommand_tokens(segment)  # pylint: disable=protected-access
    return subcommand is not None and subcommand[0] == "grep"


def _is_search_operation(tool_name: str, tool_input: dict) -> bool:
    """`agent-toolkit:search`の起動の契機とする検索の呼び出しかを返す。

    パイプラインの2番目以降の区間は標準入力を検索するため対象から外す。
    """
    if tool_name in {"Grep", "Glob"}:
        return True
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return any(_is_search_segment(pipeline[0]) for pipeline in _shell_segments.extract_execution_pipelines(command))


_ROOT_CAUSE_HEADING = re.compile(r"^## 原因分析[ \t]*$", re.MULTILINE)
"""原因分析の見出しだけから成る行。AWIの`## 原因分析`と計画ファイル（バグ）の起草で書く。"""


def _is_root_cause_writing(tool_name: str, tool_input: dict) -> bool:
    """`agent-toolkit:bugfix`の起動の契機とする原因分析の記述かを返す。

    編集ツールは変更後の断片（Claude Codeの`Write`・`Edit`・`MultiEdit`とCodexの`apply_patch`）、
    Bashはコマンド文字列に、`## 原因分析`だけから成る行がある場合を対象とする。
    """
    if tool_name == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and _ROOT_CAUSE_HEADING.search(command) is not None
    fields = _tool_input.new_content_fields(tool_name, tool_input)
    return fields is not None and any(_ROOT_CAUSE_HEADING.search(value) for _, value in fields)


def _is_managed_temp_create(tool_name: str, tool_input: dict) -> bool:
    """`agent-toolkit:managed-temp`の起動の契機とする`atk managed-temp create`の実行かを返す。

    共通のBash解析から得た実行位置をランチャーの共通定義へ渡す。
    複合文と置換の内側も同じ起動として扱い、他のコマンドの引数は起動に含めない。
    `cleanup`と`list`は対象外とする。
    """
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return any(
        is_atk_command(invocation.segment.tokens) and invocation.segment.tokens[1:3] == ("managed-temp", "create")
        for invocation in extract_bash_invocations(command)
    )


def _python_write_paths(code: str) -> list[str]:
    """読取と共用する直列の解析から、静的なPythonの書込先を返す。"""
    return python_file_paths(code, writing=True)


def _sed_write_paths(arguments: tuple[str, ...]) -> list[str]:
    """in-place編集のsedから、スクリプトとオプションの値を除いたファイル引数を返す。"""
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
    """転送先が明示されたコピー・移動・導入の書込先を返す。"""
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
    """perlのin-place編集の対象を返す。オプションの値とprogramfile以降の引数はオプションとして読まない。

    値を必須とするオプションは連結部分または次の語を値として消費し、値を任意とするオプションは同じ語だけを値とする。
    -l/-0の数値部分の後に続く-piなどはオプションとして読む。
    """
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
    """未知の展開を含むパスは、最後の展開より後ろが`/`で始まる場合だけその末尾を返し、それ以外はbasenameを推定せず`None`を返す。"""
    if STATIC_UNKNOWN not in path:
        return path
    suffix = path.rsplit(STATIC_UNKNOWN, 1)[1]
    return suffix if suffix.startswith("/") else None


def _gawk_write_paths(arguments: tuple[str, ...]) -> list[str]:
    """inplaceライブラリを指定したgawkの入力ファイルを返す。"""
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
    """tee・spongeのオプション領域と書込先を区別する。"""
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


def _bash_write_paths(command: str, cwd: str | None = None) -> list[str]:
    """実行位置のリダイレクト・tee・sponge・gawk・sed・cp・mv・install・rsync・perl・dd・Pythonの書込先を返す。

    静的な末尾だけを使い、本文と環境変数を実行して解決しない。
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

    for invocation, current in shell_cwd.bash_invocation_cwds(command, cwd or ""):
        found = [target for target in invocation.static_outputs if isinstance(target, str)]
        tokens = invocation.static_tokens
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
    return paths


def written_paths(tool_name: str, tool_input: dict, cwd: str) -> list[str]:
    """入力から確定した書込先を、操作時点のcwdに対応する絶対パスで返す。"""
    if tool_name == "Bash":
        command = tool_input.get("command")
        return _bash_write_paths(command, cwd) if isinstance(command, str) else []
    operations = _tool_input.parse_operations(tool_name, tool_input, cwd)
    return [str(pathlib.Path(operation.path).resolve()) for operation in operations or [] if operation.path]


def _is_agent_document_writing(tool_name: str, tool_input: dict) -> bool:
    """編集ツールとBashの書込先がエージェント向け文書なら真を返す。"""
    if tool_name == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and any(is_agent_doc_target_file(path) for path in _bash_write_paths(command))
    operations = _tool_input.parse_operations(tool_name, tool_input, "")
    return operations is not None and any(
        is_agent_doc_target_file(path) for operation in operations for path in operation.display_paths
    )


def _is_body_writing(tool_name: str, tool_input: dict) -> bool:
    """本文を作成・更新する編集入力と、既存のBash文書編集を判定する。"""
    if tool_name == "Bash":
        return _is_agent_document_writing(tool_name, tool_input)
    operations = _tool_input.parse_operations(tool_name, tool_input, "")
    return any(
        operation.path
        and operation.kind != _tool_input.KIND_DELETE
        and (
            bool(operation.whole_after_text)
            if operation.is_whole_write
            else any(fragment.before != fragment.after for fragment in operation.fragments)
        )
        for operation in operations or []
    )


def _is_delegation_operation(tool_name: str, _tool_input: dict) -> bool:
    """既存の委譲ツール集合から、起動・追送・観測・巻取りの操作を判定する。"""
    return tool_name in AGENT_TOOL_NAMES | AGENTS_SERVER_HOOK_TOOL_NAMES


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
    """`uv run`・`uvx`・`npx`のオプションとその値を除き、実行するコマンドからのトークン列を返す。"""
    index = 0
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 2 if tokens[index] in _UV_VALUE_OPTIONS else 1
    return tokens[index:]


def _is_task_target(target: str) -> bool:
    return target in _TASK_TARGETS or target.startswith(("test-", "test:"))


def _is_verification_tokens(tokens: Sequence[str]) -> bool:
    """1つの実行位置のトークン列が、変更範囲の検証を実行するかを返す。ヘルプ・版表示・一覧などの照会は除く。"""
    if not tokens or _QUERY_OPTIONS.intersection(tokens):
        return False
    name = pathlib.PurePath(tokens[0]).name
    rest = tokens[1:]
    if is_atk_command(tokens):
        if tuple(rest[:2]) == ("run-script", "plan-verify"):
            return True
        return tuple(rest[:1]) == ("run-command",) and "--" in rest and _is_verification_tokens(rest[rest.index("--") + 1 :])
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


def _is_verification_operation(tool_name: str, tool_input: dict) -> bool:
    """`agent-toolkit:check-execution`の起動の契機とする検証の実行かを返す。

    複合文と置換の内側も共通のBash解析から実行位置を得て判定する。保存ログの表示と状態の照会は対象外とする。
    """
    if tool_name in _VERIFICATION_MCP_TOOL_NAMES:
        return True
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    return any(_is_verification_tokens(invocation.segment.tokens) for invocation in extract_bash_invocations(command))


OPERATION_SKILLS: tuple[OperationSkill, ...] = (
    OperationSkill("agent-toolkit:delegation", "delegation", "委譲操作", _is_delegation_operation),
    OperationSkill("agent-toolkit:search", "search", "検索", _is_search_operation),
    OperationSkill("agent-toolkit:bugfix", "bugfix", "原因分析の記述", _is_root_cause_writing),
    OperationSkill("agent-toolkit:managed-temp", "managed-temp", "個別のmanaged-temp領域の作成", _is_managed_temp_create),
    OperationSkill("agent-toolkit:writing-standards", "writing-standards", "本文の作成・更新", _is_body_writing),
    OperationSkill(
        "agent-toolkit:check-execution",
        "check-execution",
        "検証の実行",
        _is_verification_operation,
        references=("references/verification-scope.md",),
    ),
)
"""操作を起動の契機とするスキルの表。"""


def canonical_skill_name(skill_name: object) -> str | None:
    """Skill起動の名前が表のスキルを指す場合に、その完全名を返す。"""
    entry = _entry(skill_name)
    return entry.skill_name if entry is not None else None


def _entry(skill_name: object) -> OperationSkill | None:
    if not isinstance(skill_name, str):
        return None
    return next((entry for entry in OPERATION_SKILLS if skill_name in {entry.skill_name, entry.short_name}), None)


def document_id(entry: OperationSkill, relative_path: str) -> str:
    """取得の記録に使う資料の識別子。`SKILL.md`はスキルの完全名、参照資料は`<完全名>:<相対パス>`とする。"""
    return entry.skill_name if relative_path == SKILL_DOCUMENT else f"{entry.skill_name}:{relative_path}"


def _mark_ready(identifier: str, agent: str) -> Callable[[dict], dict | None]:
    def _mutator(state: dict) -> dict | None:
        recorded = state.get(_rules_context.OPERATION_SKILL_READY_KEY)
        recorded = dict(recorded) if isinstance(recorded, dict) else {}
        agents = recorded.get(identifier)
        agents = list(agents) if isinstance(agents, list) else []
        if agent in agents:
            return None
        recorded[identifier] = [*agents, agent]
        state[_rules_context.OPERATION_SKILL_READY_KEY] = recorded
        return state

    return _mutator


def _missing_documents(recorded: object, entry: OperationSkill, agent: str) -> list[str]:
    """主体の取得が記録されていない必須資料の相対パスを返す。"""
    records = recorded if isinstance(recorded, dict) else {}
    missing = []
    for relative in entry.documents:
        agents = records.get(document_id(entry, relative))
        if not isinstance(agents, list) or agent not in agents:
            missing.append(relative)
    return missing


def record_skill_ready(session_id: str, skill_name: object, agent: str) -> None:
    """表のスキルのSkill起動を、呼び出し主体の`SKILL.md`の取得として記録する。"""
    entry = _entry(skill_name)
    if entry is None or not session_id:
        return
    update_state(session_id, _mark_ready(document_id(entry, SKILL_DOCUMENT), agent))


def record_document_read(session_id: str, read: _document_reads.DocumentRead, agent: str) -> None:
    """表のスキルの必須資料の全文の読取を、呼び出し主体の取得として記録する。部分取得は記録しない。"""
    entry = _entry(read.skill)
    if entry is None or not session_id or read.partial or read.relative_path not in entry.documents:
        return
    update_state(session_id, _mark_ready(document_id(entry, read.relative_path), agent))


def _subject(payload: dict) -> str:
    """主体の区分を返す。区分ごとの取得手段と記録する観測は`docs/development/design-hooks.md`の該当節が定める。"""
    if _delegated_session.is_lightweight_delegated(os.environ):
        return SUBJECT_LIGHTWEIGHT
    if is_codex_payload(payload):
        return SUBJECT_CODEX
    if _agent_id.resolve_hook_agent_id(payload) != _agent_id.MAIN_AGENT_ID:
        return SUBJECT_SUBAGENT
    return SUBJECT_MAIN


def _document_problem(path: pathlib.Path) -> str | None:
    """案内する資料を取得できない理由を返す。取得できる場合は`None`を返す。"""
    try:
        if not path.read_text(encoding="utf-8").strip():
            return "本文が空"
    except (OSError, UnicodeError) as exc:
        return str(exc)
    return None


def _acquisition_steps(subject: str, entry: OperationSkill, missing: list[str]) -> list[str]:
    """区分ごとに、未取得の資料をホスト本来の手段で取得する操作を返す。"""
    steps = []
    for relative in missing:
        path = _plugin_resources.skill_document_path(entry.short_name, relative)
        if subject == SUBJECT_CODEX:
            steps.append(f"`{path}`の全文を読む（`cat`など。`head`や範囲指定の読取は取得にならない）")
        elif relative != SKILL_DOCUMENT:
            steps.append(f"`{path}`をReadで全文読む（`offset`・`limit`を付けない）")
        elif subject == SUBJECT_SUBAGENT:
            steps.append(f"`Skill`で`{entry.skill_name}`を起動する。`Skill`を使えない場合は`{path}`をReadで全文読む")
        else:
            steps.append(f"`Skill`で`{entry.skill_name}`を起動する")
    return steps


def operation_skill_delivery(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> list[str]:
    """必須資料を取得していない主体の該当操作を止め、ホスト本来の取得手段と同じ操作の再発行を案内する。

    遮断の応答は資料の本文を含まず、遮断の時点では取得を記録しない。軽量委譲先は遮断しない。
    """
    if not session_id:
        return []
    subject = _subject(payload)
    if subject == SUBJECT_LIGHTWEIGHT:
        return []
    agent = _agent_id.resolve_hook_agent_id(payload)
    recorded = read_state(session_id).get(_rules_context.OPERATION_SKILL_READY_KEY)
    pending = [
        (entry, missing)
        for entry in OPERATION_SKILLS
        if (missing := _missing_documents(recorded, entry, agent)) and entry.matches(tool_name, tool_input)
    ]
    if not pending:
        return []
    failures = [
        f"`{path}`（{problem}）"
        for entry, missing in pending
        for relative in missing
        if (problem := _document_problem(path := _plugin_resources.skill_document_path(entry.short_name, relative)))
    ]
    if failures:
        operations = "・".join(entry.operation for entry, _ in pending)
        return [
            _block_notice(
                f"{operations}に必要な資料を取得できず、操作を実行していない。資料: {'、'.join(failures)}",
                fix="資料の読取失敗を委譲元またはユーザーへ報告し、資料の復旧後に同じ操作を再発行する。",
            )
        ]
    lines = [
        f"{entry.operation}の実行前に`{entry.skill_name}`の基準を取得する: "
        + "。".join(_acquisition_steps(subject, entry, missing))
        for entry, missing in pending
    ]
    return [
        _block_notice(
            "\n".join(lines),
            fix="上記の資料を取得し、基準を適用してから同じ操作を再発行する。取得を観測するまで同じ操作は止まる。",
        )
    ]
