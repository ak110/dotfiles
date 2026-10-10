"""CodexのBashによる大量の全文取得を共通の本文読取へ誘導する。

Codexではシェル出力の上限を超えた取得が返却本文の欠落を招き、欠落した範囲を回復できないため遮断する。
Claude Codeはホストが上限超過を`PARTIAL view`または退避ファイルとして返し、残りを続けて取得できるため対象外とする。

遮断後に対処する型のhookとする。規範は全文取得の前に容量を測る工程を求めず、
遮断した場合だけ共通の本文読取コマンドで読ませる。閾値を出力上限から導いておけば、
遮断は実際に上限を超え得るファイルだけで発火し、事前計測の呼び出しを操作のたびに払うより費用が小さい。
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from agent_toolkit._atk import read_file
from agent_toolkit._common import host_homes, shell_cwd, shell_segments
from agent_toolkit._common.bash_invocations import extract_bash_invocations
from agent_toolkit._common.uv_arguments import is_python_token
from agent_toolkit._hooks import plugin_resources
from agent_toolkit._hooks.notice import block_formatter, formatter

# Codexの配布設定の出力上限`tool_output_token_limit = 20000`（トークン）と、
# agent-toolkitのMarkdownで測った1トークンあたりバイト数の最小値3.10の積は約62,000バイトである。
# 実行セルが本文へ付加する分の余裕を取り、48KiBを閾値とする。
# 測定の記録と再検証手段は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_hooks/pretooluse/large_reads.py：全文取得の閾値：2026年9月29日」にある。
_DEFAULT_BYTE_THRESHOLD = 48 * 1024
_BYTE_THRESHOLD_ENV = "AGENT_TOOLKIT_LARGE_READ_BYTES"
_FULL_READ_COMMANDS = frozenset({"cat", "less", "more"})
_block_notice = block_formatter("pretooluse")
_notice = formatter("pretooluse")


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
    """遮断の判定と対象の説明に必要な容量だけを数える。"""
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


def _read_operands(tokens: Sequence[str], *, include_partial: bool) -> tuple[str, ...]:
    """全文取得と、配送だけが対象とする範囲取得のファイル引数を返す。"""
    if not tokens:
        return ()
    name = pathlib.PurePath(tokens[0]).name
    operands = tokens[1:]
    if include_partial and operands[:1] == ("--",):
        operands = operands[1:]
    if (
        len(tokens) >= 2
        and name in _FULL_READ_COMMANDS
        and all(
            not operand.startswith("-") and not any(marker in operand for marker in ("<", ">", "$(", "`"))
            for operand in operands
        )
    ):
        return tuple(operands)
    if (
        len(tokens) == 4
        and name == "sed"
        and tokens[1] == "-n"
        and (tokens[2] in {"p", "1,$p"} or include_partial and re.fullmatch(r"\d+(?:,[\d$]+)?p|p", tokens[2]))
    ):
        return (tokens[3],)
    if len(tokens) == 3 and name == "awk":
        program = "".join(tokens[1].split())
        if program in {"{print}", "{print$0}"}:
            return (tokens[2],)
    if include_partial and name == "atk" and tokens[1:2] == ("read-file",) and "--" in tokens:
        separator = tokens.index("--")
        options = tokens[2:separator]
        index = 0
        while index < len(options):
            option, equals, value = options[index].partition("=")
            if option not in {"--start", "--max-bytes"}:
                return ()
            if not equals:
                index += 1
                value = options[index] if index < len(options) else ""
            if not value.isdigit():
                return ()
            index += 1
        return tuple(tokens[separator + 1 :]) if len(tokens) == separator + 2 else ()
    return ()


def bash_read_paths(command: str, cwd: str, *, include_partial: bool = False) -> Iterator[tuple[pathlib.Path, ...]]:
    """読取の解析とcwd・パスの解決を共有し、コマンドごとにファイル群を返す。"""
    current = shell_cwd.CwdResolution(cwd, bool(cwd))
    for pipeline in shell_segments.extract_execution_pipelines(command):
        if len(pipeline) != 1 or not pipeline[0].resolved:
            continue
        tokens = tuple(pipeline[0].tokens)
        change = shell_cwd.resolve_cwd_change(list(tokens), current)
        if change is not None:
            current = change
            continue
        paths = []
        for operand in _read_operands(tokens, include_partial=include_partial):
            if include_partial and any(marker in operand for marker in ("$", "`", "<", ">", "*", "?")):
                continue
            if include_partial and not pathlib.Path(operand).expanduser().is_absolute() and not current.resolved:
                continue
            base = current.path if current.resolved and current.path else cwd
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
            if token[:2] in {"-e", "-f"} and len(token) > 2:
                has_pattern = True
        else:
            operands.append(token)
    return tuple(operands if has_pattern else operands[1:])


def python_file_paths(code: str, *, writing: bool = False) -> list[str]:
    """直列のリテラル・単純代入を解析し、読取と書込の静的ファイル対象を共用する。"""
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


def _bash_session_record_paths(command: str, cwd: str) -> list[pathlib.Path]:
    """実行位置と静的引数だけから、原記録の読取・本文検索・解析入力を得る。"""
    current = shell_cwd.CwdResolution(cwd, bool(cwd))
    found: list[pathlib.Path] = []
    for invocation in extract_bash_invocations(command):
        if not invocation.arguments_known:
            continue
        tokens = invocation.segment.tokens
        change = shell_cwd.resolve_cwd_change(list(tokens), current)
        if change is not None:
            current = change
            continue
        name = pathlib.PurePath(tokens[0]).name
        search = name in {"rg", "grep", "egrep", "fgrep"}
        operands = list(_read_operands(tokens, include_partial=True))
        if search:
            if "--files" in tokens or "--help" in tokens or "--version" in tokens:
                continue
            operands = list(_content_operands(tokens, pattern=True))
            if not operands and current.resolved:
                operands = [current.path]
        elif name in {*_FULL_READ_COMMANDS, "head", "tail"} and not operands:
            operands = list(_content_operands(tokens, pattern=False))
        elif name in {"sed", "awk"} and not operands:
            operands = list(_content_operands(tokens, pattern=True))
        elif is_python_token(name):
            if "-c" in tokens:
                position = tokens.index("-c")
                operands = python_file_paths(tokens[position + 1]) if position + 1 < len(tokens) else []
            elif "--help" not in tokens and "--version" not in tokens:
                operands = list(tokens[2:])
        elif name in {"jq", "node", "ruby", "perl"}:
            operands = list(tokens[2:])
        for operand in operands:
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
    reference = plugin_resources.skill_reference("writing-standards", "references/session-records.md")
    return _notice(
        "セッションの原記録の本文を、汎用ツールで直接参照しようとしている。",
        tag="warn",
        fix=(
            "`atk run-script session-review-evidence --`へ、ファイルは`--transcript <原記録の絶対パス>`、"
            "本文の検索は`--grep <正規表現>`か`--fixed-string <文字列>`、指定位置は`--detail <record:line>`、"
            "ツール入力は`--tool-calls --tool <名前> --input-regex <正規表現>`を渡す。"
            "期間探索は`--catalog-claude-project <ディレクトリ>`か`--catalog-codex-history <ディレクトリ>`へ"
            "`--since <時刻> --observation-boundary <時刻>`を加える。"
            "発話後の呼出順は`--user-events`とツール一覧、または`--bundle`の抽出済み`conversation.jsonl`から集計する。"
            f"入力の選択は{reference}に従い、抽出済みの結果を後処理する。"
        ),
        removable_cause=False,
        escalate_on_repeat=False,
    )


def _large_read_notice(path: pathlib.Path, plan: _ReadPlan, byte_threshold: int) -> str:
    if _is_session_record(path):
        return _block_notice(
            f"原セッション記録の全文取得を遮断した（{plan.byte_count}バイト、閾値: {byte_threshold}バイト）: {path}",
            fix="`atk run-script session-review-evidence -- --transcript <原記録の絶対パス>`に、目的に応じた照会条件を加える。"
            "`agent-toolkit:writing-standards`の`references/session-records.md`で入力と照会モードを選ぶ。",
        )
    return _block_notice(
        f"{plan.line_count}行、{plan.byte_count}バイトのファイルの全文取得を遮断した（閾値: {byte_threshold}バイト）: {path}",
        fix=(
            f"`{read_file.first_read_command(path)}`で取得し、`text`を連結する。"
            "`next`を`--start`へ渡し、`eof`が`true`になるまで続ける。"
        ),
    )


def _large_multi_read_notice(path_counts: Sequence[tuple[pathlib.Path, _ReadPlan]], byte_threshold: int) -> str:
    if any(_is_session_record(path) for path, _plan in path_counts):
        return _block_notice(
            "原セッション記録を含む複数ファイルの全文取得を遮断した。",
            fix="原記録は`atk run-script session-review-evidence -- --transcript <原記録の絶対パス>`へ照会条件を渡し、"
            "通常の本文は別の呼び出しで取得する。入力の選択は`agent-toolkit:writing-standards`の`references/session-records.md`に従う。",
        )
    total_bytes = sum(plan.byte_count for _path, plan in path_counts)
    details = "、".join(
        f"`{path}`: {plan.line_count}行、{plan.byte_count}バイト、`{read_file.first_read_command(path)}`"
        for path, plan in path_counts
    )
    return _block_notice(
        f"複数ファイルの全文取得を遮断した（合計: {total_bytes}バイト、閾値: {byte_threshold}バイト）: {details}",
        fix="ファイルごとに示したコマンドで取得し、`text`を連結する。`next`を`--start`へ渡し、`eof`が`true`になるまで続ける。",
    )


def check_large_bash_read(command: str, cwd: str, *, is_codex: bool = False) -> str | None:
    """Codexでパイプ・リダイレクトを持たない単純なBash全文取得のうち、閾値を超えるものだけを遮断する。"""
    if not is_codex:
        return None
    byte_threshold = _byte_threshold()
    for paths in bash_read_paths(command, cwd):
        path_counts: list[tuple[pathlib.Path, _ReadPlan]] = []
        for path in paths:
            measurement = _measure_and_plan(path)
            if measurement is not None:
                path_counts.append((path, measurement))
        if not path_counts:
            continue
        if sum(plan.byte_count for _path, plan in path_counts) > byte_threshold:
            if len(path_counts) == 1:
                path, plan = path_counts[0]
                return _large_read_notice(path, plan, byte_threshold)
            return _large_multi_read_notice(path_counts, byte_threshold)
    return None
