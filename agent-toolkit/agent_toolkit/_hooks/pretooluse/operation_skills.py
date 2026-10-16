"""操作を起動の契機とするスキルが未起動のまま、その操作を実行した呼び出しへ警告する。

スキルの起動の契機（descriptionと常時規範の参照文）はセッション開始時に配送されるが、
その操作を実行する時点には未起動であることを示す手掛かりが無く、起動されないまま本文の規定が適用されない。
本判定は操作の時点で起動記録の不在を示し、未適用の場合に基準の適用を求める。

判定の結論は警告とする。根拠は`agent-toolkit:writing-standards`の`references/claude-hooks-block-warn.md`
「遮断と警告の選択」にある。判定の入力はツール名、コマンド文字列およびSkill起動の記録だけで
機械的に確定し、発火は呼び出し主体の文脈ごとに1回であるため費用が小さい。
遮断しない理由は、スキルの起動が常時規範で努力目標とされていることと、検索が複合コマンドに含まれると
遮断で全体の再実行を要し、失うターンの作業が大きいことである。

呼び出し主体は`agent_id.resolve_hook_agent_id`で区別し、メイン会話と`Agent`ツールのサブエージェントを
別の文脈として扱う。`agents_server`の委譲先は別のセッションとして自身の状態ファイルで判定する。
起動済みの記録はセッション状態キー`rules_context.OPERATION_SKILL_READY_KEY`へ、警告を返した時点と
PostToolUse(Skill)の起動の観測時点で書く。

警告の次の操作は、`Skill`での起動と、自分のツール一覧に`Skill`が無い主体が`SKILL.md`を`Read`で全文読む操作を併記する。
`agents_server`のClaudeの軽量起動（`explore`・`write`・`shell`）は`disallowed_tools`で`Skill`を除き、スキルの一覧も届かないため、
警告が基準の本文へ到達する唯一の手掛かりになる。hookの入力は通常の委譲先と軽量起動を区別できないため、
主体ごとに本文を書き分けず、両方の手段を同じ本文に示す。Readによる適用済みを観測できないため、
起動記録が無い場合も、適用済みなら続行できる本文とする。

Codexでは警告も記録もしない。Codexはスキルを`SKILL.md`の読取で適用し、hookはその読取を起動として観測できないため、
記録の不在からは未起動と読了済みを区別できず、読了済みの操作にも警告する。Codexにはスキルの一覧と本文が配送される。

表へスキルを加える場合は、操作の判定関数とその正例・負例のテストを同じ変更単位で加える。
判定関数はツール名とツール入力だけから結果を確定し、作業の意味の解釈を要する条件を含めない。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy from collections.abc import Callable, Sequence

from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.notice import formatter as _notice_formatter
lazy from agent_toolkit._common import shell_cwd
lazy from agent_toolkit._common import shell_segments as _shell_segments
lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, extract_bash_invocations
lazy from agent_toolkit._common.heredocs import heredoc_bodies
lazy from agent_toolkit._common.session_state import read_state, update_state
lazy from agent_toolkit._common.uv_arguments import is_python_token
lazy from agent_toolkit._hooks import agent_id as _agent_id
lazy from agent_toolkit._hooks import plugin_resources as _plugin_resources
lazy from agent_toolkit._hooks import rules_context as _rules_context
lazy from agent_toolkit._hooks import tool_input as _tool_input
lazy from agent_toolkit._hooks.pretooluse import shell_checks as _shell_checks
lazy from agent_toolkit._hooks.pretooluse.agent_checks import AGENTS_SERVER_HOOK_TOOL_NAMES
lazy from agent_toolkit._hooks.pretooluse.large_reads import python_file_paths
lazy from agent_toolkit._hooks.pretooluse.task_document_launch import AGENT_TOOL_NAMES
lazy from agent_toolkit._plan.structure import is_agent_doc_target_file

_llm_notice = _notice_formatter("pretooluse")

_SEARCH_COMMANDS = frozenset({"rg", "find"})
_GREP_COMMANDS = frozenset({"grep", "egrep", "fgrep"})
_GREP_RECURSIVE_LONG_OPTIONS = frozenset({"--recursive", "--dereference-recursive"})
_GREP_RECURSIVE_SHORT_OPTIONS = frozenset("rR")
_GREP_SHORT_OPTIONS_WITH_VALUE = frozenset("efmABCDd")
"""値を取る`grep`の短縮オプション。束ねた短縮オプションの中でこの文字より後ろは値として読む。"""


@dataclasses.dataclass(frozen=True)
class OperationSkill:
    """操作を起動の契機とするスキルと、その操作の判定関数の組。"""

    skill_name: str
    short_name: str
    operation: str
    matches: Callable[[str, dict], bool]


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

    区間のトークン列にある`managed-temp`・`create`の連続を、直前のトークンが`atk`（パスを伴う形を含む）である場合と、
    `managed-temp`が区間の先頭である場合に対象とする。`extract_execution_pipelines`は`for … ; do atk …`の`do`を
    区間の先頭に残し、`d=$(atk …)`では`d=$(atk`を前置語として除くため、先頭のトークンだけでは両方の形を判定できない。
    検索語として1つの引数に含めた文字列はトークンが分かれないため対象にならない。`cleanup`と`list`は対象外とする。
    """
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    for pipeline in _shell_segments.extract_execution_pipelines(command):
        for segment in pipeline:
            tokens = segment.tokens
            for index in range(len(tokens) - 1):
                if tokens[index] != "managed-temp" or tokens[index + 1] != "create":
                    continue
                if index == 0 or pathlib.PurePath(tokens[index - 1]).name == "atk":
                    return True
    return False


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
    current = shell_cwd.CwdResolution(cwd or "", bool(cwd))

    def append_paths(found: list[str], base: shell_cwd.CwdResolution) -> None:
        for raw in found:
            value = _static_write_path(raw)
            if value is None:
                continue
            path = pathlib.Path(value).expanduser()
            if cwd is None:
                paths.append(value)
            elif path.is_absolute():
                paths.append(str(path.resolve()))
            elif base.resolved:
                paths.append(str((pathlib.Path(base.path) / path).resolve()))

    for invocation in extract_bash_invocations(command):
        change = shell_cwd.resolve_cwd_change(list(invocation.segment.tokens), current)
        if change is not None:
            current = change
            continue
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
        header = command[: body.start].rstrip("\r\n").rsplit("\n", 1)[-1]
        invocations = extract_bash_invocations(header)
        if len(invocations) == 1:
            tokens = invocations[0].segment.tokens
            if is_python_token(pathlib.PurePath(tokens[0]).name) and tokens[1:] in {(), ("-",)}:
                base = shell_cwd.CwdResolution(cwd or "", bool(cwd))
                for preceding in extract_bash_invocations(command[: body.start]):
                    change = shell_cwd.resolve_cwd_change(list(preceding.segment.tokens), base)
                    if change is not None:
                        base = change
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


def _is_delegation_operation(tool_name: str, _tool_input: dict) -> bool:
    """既存の委譲ツール集合から、起動・追送・観測・巻取りの操作を判定する。"""
    return tool_name in AGENT_TOOL_NAMES | AGENTS_SERVER_HOOK_TOOL_NAMES


OPERATION_SKILLS: tuple[OperationSkill, ...] = (
    OperationSkill("agent-toolkit:delegation", "delegation", "委譲操作", _is_delegation_operation),
    OperationSkill("agent-toolkit:search", "search", "検索", _is_search_operation),
    OperationSkill("agent-toolkit:bugfix", "bugfix", "原因分析の記述", _is_root_cause_writing),
    OperationSkill("agent-toolkit:managed-temp", "managed-temp", "個別のmanaged-temp領域の作成", _is_managed_temp_create),
    OperationSkill(
        "agent-toolkit:writing-standards", "writing-standards", "エージェント向け文書の編集", _is_agent_document_writing
    ),
)
"""操作を起動の契機とするスキルの表。"""


def canonical_skill_name(skill_name: object) -> str | None:
    """Skill起動の名前が表のスキルを指す場合に、その完全名を返す。"""
    if not isinstance(skill_name, str):
        return None
    for entry in OPERATION_SKILLS:
        if skill_name in {entry.skill_name, entry.short_name}:
            return entry.skill_name
    return None


def _mark_ready(skill_name: str, agent: str) -> Callable[[dict], dict | None]:
    def _mutator(state: dict) -> dict | None:
        recorded = state.get(_rules_context.OPERATION_SKILL_READY_KEY)
        recorded = dict(recorded) if isinstance(recorded, dict) else {}
        agents = recorded.get(skill_name)
        agents = list(agents) if isinstance(agents, list) else []
        if agent in agents:
            return None
        recorded[skill_name] = [*agents, agent]
        state[_rules_context.OPERATION_SKILL_READY_KEY] = recorded
        return state

    return _mutator


def _is_ready(session_id: str, skill_name: str, agent: str) -> bool:
    recorded = read_state(session_id).get(_rules_context.OPERATION_SKILL_READY_KEY)
    if not isinstance(recorded, dict):
        return False
    agents = recorded.get(skill_name)
    return isinstance(agents, list) and agent in agents


def record_skill_ready(session_id: str, skill_name: object, agent: str) -> None:
    """表のスキルの起動を、呼び出し主体の文脈について起動済みとして記録する。"""
    canonical = canonical_skill_name(skill_name)
    if canonical is None or not session_id:
        return
    update_state(session_id, _mark_ready(canonical, agent))


def _warning(entry: OperationSkill) -> str:
    skill_md = _plugin_resources.skill_reference(entry.short_name, "SKILL.md")
    return _llm_notice(
        f"{entry.operation}の呼び出しで、`{entry.skill_name}`の起動記録を確認できない。",
        tag=_WARN_TAG,
        fix=(
            f"既に本文を読み基準を適用している場合は、そのまま続ける。"
            f"未適用の場合は、ツール`Skill`で`{entry.skill_name}`を起動し、同スキルの基準で今回の{entry.operation}の手段と範囲を確かめ直す。"
            f"ツール一覧に`Skill`が無い主体は、"
            f"{skill_md}を`Read`で全文読んで同じ基準を適用する。"
            f"基準に合わない{entry.operation}の結果は使わず、基準に合う{entry.operation}でやり直す。"
        ),
        removable_cause=False,
    )


def operation_skill_warnings(payload: dict, tool_name: str, tool_input: dict, session_id: str, *, is_codex: bool) -> list[str]:
    """未起動のスキルの操作を実行する呼び出しへ、文脈ごとに1回だけ警告本文を返す。

    警告を返した時点でその文脈のそのスキルを起動済みとして記録する。
    `session_id`が無い呼び出しは記録できず文脈ごとの1回を保てないため、判定しない。
    Codexの呼び出しは起動済みを観測できないため、警告も記録もしない。
    """
    if not session_id or is_codex:
        return []
    agent = _agent_id.resolve_hook_agent_id(payload)
    warnings: list[str] = []
    for entry in OPERATION_SKILLS:
        if _is_ready(session_id, entry.skill_name, agent) or not entry.matches(tool_name, tool_input):
            continue
        # 並行する呼び出しが同時に判定しても、記録を書き込んだ1件だけが警告する。
        if update_state(session_id, _mark_ready(entry.skill_name, agent)):
            warnings.append(_warning(entry))
    return warnings
