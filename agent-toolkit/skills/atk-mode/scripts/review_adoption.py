"""保存済みの実行レビュー指摘管理表から、指摘の採用・不採用を期間ごとに集計する。

振り返りのbatchが、不採用の多いレビュー担当のモデルやlevel、同じ理由の不採用の反復を改善候補にするための入力を返す。
表の読込と期間の割当は`cost_metrics.saved_review_tables`を共有する。
レビュー担当のモデルは、表のファイル名を本文に含むセッション記録から、役割の配送標識か組み込み委譲の記録を使って求める。
"""

from __future__ import annotations

import argparse
import ast
import collections
import datetime
import json
import posixpath
import re
import shlex
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, NamedTuple
from urllib.parse import unquote, urlsplit

from agent_toolkit._atk import review_table_format
from agent_toolkit._common import heredocs, host_homes, private_notes, session_record_paths
from agent_toolkit._common.bash_invocations import (
    STATIC_UNKNOWN,
    BashInvocation,
    extract_bash_effects,
    is_read_predicate,
    is_unknown_word,
)
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.runtime_identity import distinct_identities
from agent_toolkit._common.shell_segments import shell_c_argument
from agent_toolkit._common.shell_tokens import is_atk_command
from cost_metrics import _history, saved_review_tables
from session_evidence_execution import command_execution_input
from session_evidence_stats import _parse_cli_timestamp

_ROLE_MARKER = re.compile(r"出所: \S*(?:share|skills/atk-mode/roles)/exec-review\.subagent\.md")
# Codexの記録で、モデルの出力として現れる`response_item`の種類。これより前の入力だけを依頼文の候補にする。
_CODEX_OUTPUT_TYPES = frozenset({"reasoning", "function_call", "custom_tool_call", "local_shell_call", "web_search_call"})
_CODEX_TOOL_TYPES = frozenset({"function_call", "custom_tool_call", "local_shell_call", "web_search_call"})
_ROLE_DOCUMENT = re.compile(r"(?<![\w.-])roles/exec-review\.subagent\.md(?![\w.-])")
_ASSIGNED = re.compile(r'<atk-auto source="agents-server" kind="delivery">\s*(.*?)</atk-auto>', re.DOTALL)
_REVIEW_PATH = re.compile(r'((?:[A-Za-z]:)?[/\\][^\s"\x60]+?)(\.md|\.exec-review\.tsv)(?=\s|$|["\x60])')
_ADDED = re.compile(r"^成功: 指摘行を[1-9][0-9]*件追加した: (.+\.exec-review\.tsv)（[0-9]+件）$", re.MULTILINE)
_ADD_RECEIPT = re.compile(r"^成功: 指摘行を([1-9][0-9]*)件追加した: (.+\.exec-review\.tsv)（[0-9]+件）$", re.MULTILINE)
_CARRIED_RECEIPT = re.compile(
    r"^成功: 再確認を記録した: (.+\.exec-review\.tsv)（row-id ([1-9][0-9]{0,17})、"
    r"再確認: ラウンド([1-9][0-9]*) (対応不十分|未対応) 行[1-9][0-9]*）$",
    re.MULTILINE,
)
_RECHECK_DONE_RECEIPT = re.compile(
    r"成功: 再確認を記録した: (.+\.exec-review\.tsv)（row-id ([1-9][0-9]*)、再確認: ラウンド([1-9][0-9]*) 完了）"
)
_VALIDATE_RECEIPT = re.compile(r"成功: (?:構造|応答欄を含めて)を?検証した: (.+\.exec-review\.tsv)（[0-9]+件）")
_RESULT_TABLE = re.compile(r"^(?:- )?レビュー指摘管理表: (.+?\.exec-review\.tsv)`?(?:（[^\n]*）)?$", re.MULTILINE)
_RESULT_ORIGINAL = re.compile(r"^(?:- )?正式原本: ([^\n]+)$", re.MULTILINE)
_MODEL_UNKNOWN = "モデル不明"
_LOCATION, _LEVEL, _NO_RESPONSE = 2, 4, 6


def _input_texts(entry: dict[str, Any]) -> list[str] | None:
    message = entry.get("message")
    payload = entry.get("payload")
    content: Any = None
    if entry.get("type") == "assistant":
        return None
    if entry.get("type") == "user" and isinstance(message, dict):
        content = message.get("content")
    elif entry.get("type") == "response_item" and isinstance(payload, dict):
        if payload.get("type") in _CODEX_OUTPUT_TYPES:
            return None
        if payload.get("type") == "message":
            if payload.get("role") == "assistant":
                return None
            if payload.get("role") == "user":
                content = payload.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return []


class _RecordRole(NamedTuple):
    thread: str | None = None
    marked: bool = False
    review_parent: str | None = None
    requested_tables: tuple[str, ...] = ()


def _requested_tables(texts: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                plan.replace("\\", "/") + ".exec-review.tsv"
                for text in texts
                for match in _ASSIGNED.finditer(text)
                for prompt in (match[1],)
                if "独立実行レビュー" in prompt.partition("\n")[0]
                for plan, _extension in _REVIEW_PATH.findall(prompt)
            }
        )
    )


def _meta(payload: object) -> tuple[str | None, str | None]:
    """Codex記録のスレッドIDと、組み込み委譲による親IDを得る。"""
    if not isinstance(payload, dict):
        return None, None
    thread = payload.get("id")
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return thread if isinstance(thread, str) and thread else None, parent if isinstance(parent, str) and parent else None


def _calls_role_document(payload: dict[str, Any]) -> bool:
    if payload["type"] == "local_shell_call":
        action = payload.get("action")
        command = action.get("command") if isinstance(action, dict) else None
        return isinstance(command, list) and any(isinstance(arg, str) and _ROLE_DOCUMENT.search(arg) for arg in command)
    command = payload.get("arguments") if payload["type"] == "function_call" else payload.get("input")
    return isinstance(command, str) and _ROLE_DOCUMENT.search(command) is not None


def _record_role(path: Path) -> _RecordRole:
    thread = parent = None
    before_output = True
    requested: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "session_meta" and before_output and thread is None:
                    thread, parent = _meta(entry.get("payload"))
                if before_output:
                    texts = _input_texts(entry)
                    if texts is None:
                        before_output = False
                        if parent is None:
                            break
                    elif any(_ROLE_MARKER.search(text) for text in texts):
                        return _RecordRole(thread, marked=True)
                    else:
                        requested.update(_requested_tables(texts))
                payload = entry.get("payload")
                if (
                    parent
                    and entry.get("type") == "response_item"
                    and isinstance(payload, dict)
                    and payload.get("type") in _CODEX_TOOL_TYPES
                ):
                    return _RecordRole(
                        thread,
                        review_parent=parent if _calls_role_document(payload) else None,
                        requested_tables=tuple(sorted(requested)),
                    )
    except OSError:
        return _RecordRole()
    return _RecordRole(thread, requested_tables=tuple(sorted(requested)))


def _review_records(roots: list[Path]) -> dict[Path, tuple[tuple[str, ...] | None, bool]]:
    roles: dict[Path, _RecordRole] = {}
    by_thread: dict[str, set[_RecordRole]] = collections.defaultdict(set)
    for root in roots:
        for path in sorted(root.rglob("*.jsonl")):
            if path in roles or not path.is_file():
                continue
            role = roles[path] = _record_role(path)
            if role.thread:
                by_thread[role.thread].add(role)

    result: dict[Path, tuple[tuple[str, ...] | None, bool]] = {}
    for path, role in roles.items():
        if role.marked or (
            role.review_parent != role.thread
            and (parents := by_thread.get(role.review_parent or ""))
            and not any(parent.marked or parent.review_parent for parent in parents)
        ):
            result[path] = None, role.marked
        elif role.requested_tables:
            result[path] = role.requested_tables, False
    return result


def _successful_output(value: object) -> str:
    items = value if isinstance(value, list) else [value]
    for item in items:
        raw = item.get("text") if isinstance(item, dict) else item
        try:
            result = json.loads(raw) if isinstance(raw, str) else None
        except ValueError, RecursionError:
            continue
        if not isinstance(result, dict):
            continue
        code = result.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool) and code == 0 and isinstance(result.get("output"), str):
            return result["output"]
    return ""


def _formal_result(text: str) -> bool:
    return (
        "状態: completed" in text.splitlines()
        and re.search(r"^レビューHEAD: [a-f0-9]{7,40}$", text, re.MULTILINE) is not None
        and re.search(r"^未解決の指摘数: [0-9]+$", text, re.MULTILINE) is not None
    )


def _reported_paths(pattern: re.Pattern[str], body: str) -> set[str]:
    return {path.strip().strip("`").replace("\\", "/") for path in pattern.findall(body)}


def _read_original_name(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if tokens[:2] == ["cat", "--"]:
        tokens.pop(1)
    if len(tokens) == 2 and tokens[0] == "cat" and (Path(tokens[1]).is_absolute() or PureWindowsPath(tokens[1]).is_absolute()):
        return tokens[1].replace("\\", "/")
    return None


def _confirmed_free_review_tables(text: str, requested: set[str]) -> set[str]:
    calls: dict[str, str] = {}
    registered: set[str] = set()
    completed: set[str] = set()
    inspected: dict[str, set[str]] = collections.defaultdict(set)
    for line in split_lines(text):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = entry.get("payload") if isinstance(entry, dict) and entry.get("type") == "event_msg" else None
        command_record = event.get("item") if isinstance(event, dict) and event.get("type") == "item_completed" else None
        if isinstance(command_record, dict) and command_record.get("type") == "CommandExecution":
            original = _read_original_name(command_execution_input(command_record.get("command", "")))
            output = command_record.get("aggregated_output")
            if (
                original is not None
                and command_record.get("exit_code") == 0
                and isinstance(output, str)
                and _formal_result(output)
            ):
                inspected[original].update(_reported_paths(_RESULT_TABLE, output) & requested & registered)
            continue
        payload = entry.get("payload") if isinstance(entry, dict) and entry.get("type") == "response_item" else None
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "message" and payload.get("role") == "assistant" and payload.get("phase") == "final_answer":
            content = payload.get("content")
            if not isinstance(content, list):
                continue
            message = "\n".join(
                block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)
            )
            message = "\n".join(line.rstrip() for line in split_lines(message))
            if _formal_result(message):
                completed.update(_reported_paths(_RESULT_TABLE, message) & requested & registered)
            if "状態: completed" in split_lines(message):
                for path in _reported_paths(_RESULT_ORIGINAL, message):
                    completed.update(inspected.get(path, set()) & registered)
            continue
        if not isinstance(call := payload.get("call_id"), str):
            continue
        if kind in _CODEX_TOOL_TYPES:
            command = payload.get("input", payload.get("arguments"))
            if isinstance(command, str):
                calls[call] = command
        elif kind in {"custom_tool_call_output", "function_call_output"} and call in calls:
            command = calls.pop(call)
            output = _successful_output(payload.get("output"))
            if re.search(r"\breview-table add\b[^\n]*\s--track(?:\s+|=)exec-review(?:\s|$)", command):
                registered.update(path.replace("\\", "/") for path in _ADDED.findall(output))
            if _formal_result(output):
                reported = _reported_paths(_RESULT_TABLE, output + "\n" + command) & requested & registered
                originals = _reported_paths(_RESULT_ORIGINAL, output + "\n" + command)
                if original := _read_original_name(command):
                    originals.add(original)
                for original in originals:
                    inspected[original].update(reported)
    return {path.rsplit("/", 1)[-1] for path in completed}


def _issue_key(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def _absolute_path(value: str) -> str | None:
    if STATIC_UNKNOWN in value or is_unknown_word(value):
        return None
    if PureWindowsPath(value).is_absolute():
        return PureWindowsPath(value).as_posix()
    return PurePosixPath(value).as_posix() if value.startswith("/") else None


def _invalidate_file_write(files: dict[str, str], path: str | None, *, posix: bool = False) -> None:
    if path is None:
        files.clear()
        return

    def alias(value: str) -> str:
        normalized = posixpath.normpath(value)
        return "/" + normalized.lstrip("/") if posix and normalized.startswith("/") else normalized

    # 見かけ上同じ場所は書込証拠の失効だけに使い、本文を取得する鍵には使わない。  # pyfltr: allow-comment
    key = alias(path)
    for previous in tuple(files):
        if alias(previous) == key:
            del files[previous]


def _python_value(node: ast.AST, names: dict[str, Any], argv: list[str]) -> Any:
    match node:
        case ast.Constant(value=str() | int() as value):
            return value
        case ast.Name(id=name) if name in names:
            return names[name]
        case ast.List(elts=items):
            return [_python_value(item, names, argv) for item in items]
        case ast.Dict(keys=keys, values=values) if all(
            isinstance(key, ast.Constant) and isinstance(key.value, str) for key in keys
        ):
            return {
                key.value: _python_value(value, names, argv)
                for key, value in zip(keys, values, strict=True)
                if isinstance(key, ast.Constant)
            }
        case ast.Call(func=ast.Name(id="dict"), args=[], keywords=items) if all(item.arg is not None for item in items):
            return {item.arg: _python_value(item.value, names, argv) for item in items}
        case ast.JoinedStr(values=parts):
            return "".join(str(_python_value(part, names, argv)) for part in parts)
        case ast.FormattedValue(value=ast.Name(id=name), conversion=-1, format_spec=None) if name in names and isinstance(
            names[name], (str, PurePosixPath)
        ):
            return names[name]
        case ast.Subscript(
            value=ast.Attribute(value=ast.Name(id="sys"), attr="argv"), slice=ast.Constant(value=int() as index)
        ):
            return argv[index]
        case ast.Call(func=ast.Name(id="Path"), args=[argument], keywords=[]):
            return PurePosixPath(_python_value(argument, names, argv))
        case ast.Call(
            func=ast.Name(id="map"),
            args=[
                ast.Attribute(value=ast.Name(id="pathlib"), attr="Path"),
                ast.Subscript(
                    value=ast.Attribute(value=ast.Name(id="sys"), attr="argv"),
                    slice=ast.Slice(lower=ast.Constant(value=1), upper=None, step=None),
                ),
            ],
            keywords=[],
        ):
            return tuple(map(PurePosixPath, argv[1:]))
        case ast.BinOp(left=left, op=ast.Div(), right=right):
            parent, child = (_python_value(arg, names, argv) for arg in (left, right))
            if isinstance(parent, PurePosixPath) and isinstance(child, str):
                return parent / child
    raise ValueError


def _python_items_file(source: str, argv: list[str]) -> tuple[str, str] | None:
    # 記録されたPythonを実行せず、引数とリテラルで構成したJSONの書込だけを確定する。  # pyfltr: allow-comment
    try:
        module = ast.parse(source)
        names: dict[str, Any] = {}
        for node in module.body[:-1]:
            match node:
                case ast.Import(names=imports) if all(
                    alias.name in {"json", "pathlib", "sys"} and alias.asname is None for alias in imports
                ):
                    pass
                case ast.ImportFrom(module="pathlib", names=[ast.alias(name="Path", asname=None)], level=0):
                    pass
                case ast.Assign(targets=[ast.Name(id=name)], value=value) if name not in {
                    "json",
                    "pathlib",
                    "sys",
                    "Path",
                    "map",
                    "dict",
                }:
                    names[name] = _python_value(value, names, argv)
                case ast.Assign(targets=[ast.Tuple(elts=targets)], value=value) if all(
                    isinstance(target, ast.Name) and target.id not in {"json", "pathlib", "sys", "Path", "map", "dict"}
                    for target in targets
                ):
                    for target, item in zip(targets, _python_value(value, names, argv), strict=True):
                        if isinstance(target, ast.Name):
                            names[target.id] = item
                case _:
                    return None
        match module.body[-1]:
            case ast.Expr(value=ast.Call(func=ast.Attribute(value=target, attr="write_text"), args=[argument], keywords=[])):
                value = (
                    argument.left
                    if isinstance(argument, ast.BinOp)
                    and isinstance(argument.op, ast.Add)
                    and isinstance(argument.right, ast.Constant)
                    and argument.right.value == "\n"
                    else argument
                )
                match value:
                    case ast.Call(
                        func=ast.Attribute(value=ast.Name(id="json"), attr="dumps"), args=[items], keywords=options
                    ) if all(
                        option.arg in {"ensure_ascii", "indent"} and isinstance(option.value, ast.Constant)
                        for option in options
                    ):
                        path = _absolute_path(str(_python_value(target, names, argv)))
                        data = _python_value(items, names, argv)
                        if path is not None and isinstance(data, list):
                            return path, json.dumps(data, ensure_ascii=False)
    except IndexError, TypeError, ValueError, SyntaxError, RecursionError:
        pass
    return None


def _literal_writes(command: str, effects: list[BashInvocation]) -> dict[int, tuple[str, str]]:
    bodies = heredocs.heredoc_bodies(command)
    inline = [index for index, effect in enumerate(effects) if effect.inline_input]
    if len(inline) != len(bodies) or "<<<" in heredocs.mask_heredoc_bodies(command):
        return {}
    writes: dict[int, tuple[str, str]] = {}
    for index, body in zip(inline, bodies, strict=True):
        effect = effects[index]
        tokens, stdout = effect.variable_tokens, effect.variable_outputs[0]
        head = command[command.rfind("\n", 0, body.start - 1) + 1 : body.start]
        if (
            body.expands
            or not effect.assignments_definite
            or body.end == body.terminator_end
            or "<<-" in head
            or effect.cwd_scope
            or effect.background
            or effect.captured
        ):
            continue
        content = command[body.start : body.end]
        if tokens == ("cat",) and isinstance(stdout, str) and (path := _absolute_path(stdout)) and ">>" not in head:
            writes[index] = path, content
        elif (
            len(tokens) >= 3
            and re.fullmatch(r"python(?:3(?:\.\d+)?)?", PurePosixPath(tokens[0]).name)
            and tokens[1] == "-"
            and all(_absolute_path(arg) for arg in tokens[2:])
            and (result := _python_items_file(content, list(tokens[1:])))
        ):
            writes[index] = result
    return writes


def _review_options(args: tuple[str, ...]) -> dict[str, str]:
    options: dict[str, str] = {}
    cursor = 0
    while cursor < len(args):
        key, equals, value = args[cursor].partition("=")
        cursor += 1
        if not key.startswith("--") or key in options:
            return {}
        if not equals:
            if cursor == len(args) or args[cursor].startswith("--"):
                return {}
            value = args[cursor]
            cursor += 1
        options[key] = value
    return options


def _add_bodies(options: dict[str, str], files: dict[str, str], count: str) -> list[str]:
    if options.get("--track") != "exec-review":
        return []
    if "--items-file" not in options:
        issue = files.get(_absolute_path(options.get("--issue-file", "")) or "")
        return [issue] if issue is not None and count == "1" else []
    try:
        items = json.loads(files.get(_absolute_path(options["--items-file"]) or "", ""))
    except ValueError, RecursionError:
        return []
    if not isinstance(items, list) or str(len(items)) != count:
        return []
    if all(isinstance(item, dict) and isinstance(item.get("issue"), str) for item in items):
        return [item["issue"] for item in items]
    return []


def _read_only_command(tokens: tuple[str, ...]) -> bool:
    match tokens:
        case ("cat" | "cmp" | "cut" | "printf" | "realpath" | "set" | "test" | "wc" | "[" | ":", *_):
            return True
        case ("sed", "-n", expression, *_) if re.fullmatch(r"[0-9]+(?:,[0-9]+)?p", expression):
            return True
        case ("git", "diff", *args):
            return not any(arg.startswith(("--output", "--ext-diff")) for arg in args)
        case _:
            return is_read_predicate(tokens)


def _expected_receipt(action: str, table: str, args: tuple[str, ...], line: str) -> bool:
    if action == "add":
        return bool((match := _ADD_RECEIPT.fullmatch(line)) and _absolute_path(match[2]) == table)
    if action == "recheck":
        options = _review_options(args)
        if match := _CARRIED_RECEIPT.fullmatch(line):
            verdict = match[4]
        else:
            match = _RECHECK_DONE_RECEIPT.fullmatch(line)
            verdict = review_table_format.RECHECK_DONE
        return bool(
            match
            and _absolute_path(match[1]) == table
            and match[2] == options.get("--row-id")
            and match[3] == options.get("--round")
            and verdict == options.get("--verdict")
        )
    if action == "validate":
        return bool((match := _VALIDATE_RECEIPT.fullmatch(line)) and _absolute_path(match[1]) == table)
    if action != "show" or _review_options(args).get("--format") != "summary":
        return False
    try:
        summary = json.loads(line)
    except ValueError, RecursionError:
        return False
    return (
        isinstance(summary, dict)
        and summary.get("track") == "exec-review"
        and {"round", "total_count", "unanswered_count", "answered_count"} <= summary.keys()
    )


class _ProvenPrefix(NamedTuple):
    stop: int
    receipts: dict[int, str]


def _proven_prefix(effects: list[BashInvocation], writes: dict[int, tuple[str, str]], output: str) -> _ProvenPrefix | None:
    lines = iter(output.splitlines())
    receipts: dict[int, str] = {}
    errexit = False
    for index, effect in enumerate(effects):
        tokens = effect.variable_tokens
        stdout, stderr = effect.variable_outputs
        if not tokens and effect.standalone_assignments and effect.assignments_definite and (stdout, stderr) == (1, 2):
            continue
        if effect.background or effect.captured or effect.cwd_scope or stderr != 2 or not tokens:
            return None
        if tokens in {("set", "-e"), ("set", "-eu"), ("set", "-euo", "pipefail")} and stdout == 1:
            errexit = True
            continue
        if index in writes and ((tokens == ("cat",) and isinstance(stdout, str)) or errexit and stdout == 1):
            continue
        if tokens[0] == "printf" and isinstance(stdout, str) and _absolute_path(stdout) and not effect.inline_input:
            continue
        if len(tokens) < 4 or tokens[1] != "review-table" or not is_atk_command(tokens) or stdout != 1:
            return None
        action, table = tokens[2], _absolute_path(tokens[3])
        line = next(lines, "")
        if table is None or not _expected_receipt(action, table, tokens[4:], line):
            return None
        if action in {"add", "recheck"}:
            receipts[index] = line
        if action == "show":
            # ここから後の出力は書込の成功も登録の受領も保証しない。  # pyfltr: allow-comment
            return _ProvenPrefix(index + 1, receipts)
    return _ProvenPrefix(len(effects), receipts) if next(lines, None) is None else None


def _registered_command_issues(command: str, cwd: str, output: str, files: dict[str, str]) -> set[str]:
    unc_cwd = cwd.startswith("\\\\")
    if cwd.startswith("file:"):
        try:
            uri = urlsplit(cwd)
        except ValueError:
            files.clear()
            return set()
        unc_cwd = bool(uri.netloc)
        cwd = unquote(uri.path) if not (uri.netloc or uri.query or uri.fragment) else ""
    if cwd.startswith("/"):
        # CommandExecutionのcwdを使い、記録中のパスを現環境で解決しない。  # pyfltr: allow-comment
        head, separator, tail = command.partition("\n")
        head = re.sub(
            r'(^|;\s*)([A-Za-z_][A-Za-z_0-9]*=)\$\((?:realpath \.\.|dirname "\$PWD")\)(?=[; \t]|$)',
            lambda match: match[1] + match[2] + shlex.quote(str(PurePosixPath(cwd).parent)),
            head,
        )
        command = f"PWD={shlex.quote(cwd)}\n{head}{separator}{tail}"
    effects = extract_bash_effects(command)
    if not effects:
        files.clear()
    writes = _literal_writes(command, effects)
    proven = _proven_prefix(effects, writes, output)
    posix = not unc_cwd and re.match(r"^/?[A-Za-z]:", cwd) is None
    found: dict[tuple[str, ...], list[str]] = {}
    ambiguous: set[tuple[str, ...]] = set()
    for index, effect in enumerate(effects):
        tokens = effect.variable_tokens
        for target in effect.variable_outputs:
            if isinstance(target, str):
                _invalidate_file_write(files, _absolute_path(target), posix=posix)
        if index in writes:
            path, content = writes[index]
            _invalidate_file_write(files, path, posix=posix)
            if proven is not None and index < proven.stop:
                files[path] = content
        if not tokens:
            continue
        if len(tokens) < 4 or tokens[1] != "review-table" or not is_atk_command(tokens):
            if index not in writes and not _read_only_command(tokens):
                files.clear()
            continue
        if effect.cwd_scope or effect.background or effect.captured:
            files.clear()
            continue
        action, raw = tokens[2], tokens[3]
        table = _absolute_path(raw)
        if table is None or not table.endswith(".exec-review.tsv"):
            continue
        options = _review_options(tokens[4:])
        bodies: list[str] = []
        key = (action, table)
        receipt = proven.receipts.get(index, "") if proven is not None else ""
        if action == "add" and (added := _ADD_RECEIPT.fullmatch(receipt)):
            bodies = _add_bodies(options, files, added[1])
        elif action == "recheck":
            key = (*key, *(options.get(arg, "") for arg in ("--row-id", "--round", "--verdict")))
            if carried := _CARRIED_RECEIPT.fullmatch(receipt):
                path = _absolute_path(options.get("--issue-file", ""))
                if path is not None and path in files:
                    bodies = [review_table_format.carried_issue(files[path], int(carried[2]))]
        if key in found:
            ambiguous.add(key)
        found[key] = bodies
    return {
        key
        for registration, bodies in found.items()
        if registration not in ambiguous
        for body in bodies
        if (key := _issue_key(body))
    }


def _command_input(value: object) -> str:
    if isinstance(value, list) and all(isinstance(arg, str) for arg in value):
        return shell_c_argument(value) or shlex.join(value)
    return value if isinstance(value, str) else ""


def _registered_issues(text: str) -> set[str]:
    if "review-table" not in text:
        return set()
    files: dict[str, str] = {}
    issues: set[str] = set()
    for line in split_lines(text):
        if '"item_completed"' not in line or not any(kind in line for kind in ('"CommandExecution"', '"FileChange"')):
            continue
        try:
            entry = json.loads(line)
        except ValueError, RecursionError:
            continue
        event = entry.get("payload") if isinstance(entry, dict) and entry.get("type") == "event_msg" else None
        item = event.get("item") if isinstance(event, dict) and event.get("type") == "item_completed" else None
        if not isinstance(item, dict):
            continue
        if item.get("status") != "completed":
            files.clear()
            continue
        if item.get("type") == "FileChange" and isinstance(changes := item.get("changes"), dict):
            for raw, value in changes.items():
                path = _absolute_path(raw)
                _invalidate_file_write(files, path, posix=raw.startswith("/"))
                if (
                    path is not None
                    and isinstance(value, dict)
                    and value.get("type") == "add"
                    and isinstance(body := value.get("content"), str)
                ):
                    files[path] = body
        elif item.get("type") == "CommandExecution":
            output, cwd, code = (item.get(key) for key in ("aggregated_output", "cwd", "exit_code"))
            command = _command_input(item.get("command"))
            if (
                not isinstance(output, str)
                or not isinstance(cwd, str)
                or not isinstance(code, int)
                or isinstance(code, bool)
                or code != 0
            ):
                files.clear()
                continue
            if command and ("review-table" in command or files or "<<" in command):
                issues.update(_registered_command_issues(command, cwd, output, files))
    return issues


def _record_identity(path: Path, text: str) -> str:
    runtime = "codex" if "/.codex/" in path.as_posix() or path.name.startswith("rollout-") else "claude"
    entries = []
    for line in split_lines(text):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    identities = distinct_identities(entries, runtime)
    return ",".join(identity.machine for identity in identities) or _MODEL_UNKNOWN


def _table_models(names: list[str], roots: list[Path], rows_by_table: dict[str, list[str]] | None = None) -> dict[str, str]:
    if not names:
        return {}
    models: dict[str, set[str]] = collections.defaultdict(set)
    names_set = set(names)
    lengths = {len(name) for name in names_set}
    table_suffix = re.compile(r"\.exec-review\.tsv")
    writers: dict[str, dict[Path, str]] = collections.defaultdict(dict)
    rows_by_table = rows_by_table or {}
    wanted = {issue for issues in rows_by_table.values() for issue in issues if issue}
    for record, (requested, marked) in _review_records(roots).items():
        try:
            text = record.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        matched = (
            _confirmed_free_review_tables(text, {path for path in requested if path.rsplit("/", 1)[-1] in names_set})
            if requested is not None
            else {
                text[end - length : end]
                for suffix in table_suffix.finditer(text)
                for end in (suffix.end(),)
                for length in lengths
                if length <= end and text[end - length : end] in names_set
            }
        )
        issues = _registered_issues(text) & wanted if marked and wanted else set()
        if not matched and not issues:
            continue
        identity = _record_identity(record, text)
        for name in matched:
            models[name].add(identity)
        for issue in issues:
            writers[issue][record] = identity
    for name, issues in rows_by_table.items():
        if name in models or not issues or any(len(writers.get(issue, {})) != 1 for issue in issues):
            continue
        records = {next(iter(writers[issue].items())) for issue in issues}
        if len(records) == 1:
            models[name].add(next(iter(records))[1])
    return {name: ",".join(sorted(values)) for name, values in models.items()}


def _empty_counts() -> dict[str, int]:
    return {"adopted": 0, "rejected": 0, "transferred": 0, "unanswered": 0}


def _period_events(
    period: str, since: datetime.datetime, until: datetime.datetime, tables: list[Any], models: dict[str, str]
) -> list[dict[str, Any]]:
    summary: dict[str, Any] = {
        "kind": "review-adoption-period",
        "period": period,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "table_count": len(tables),
        "excluded_judgment_count": 0,
        "excluded_judgments": [],
        "unknown_model_tables": [],
        **_empty_counts(),
        "by_level": collections.defaultdict(_empty_counts),
        "by_model": collections.defaultdict(lambda: {**_empty_counts(), "tables": 0}),
        "by_reason": collections.Counter(),
    }
    rejections: list[dict[str, Any]] = []
    for table in tables:
        model = models.get(table.path.name, _MODEL_UNKNOWN)
        summary["by_model"][model]["tables"] += 1
        if model == _MODEL_UNKNOWN:
            summary["unknown_model_tables"].append(str(table.path.absolute()))
        summary["excluded_judgment_count"] += len(table.judgment_rows)
        summary["excluded_judgments"].extend({"table": str(table.path), "row": number} for number, _ in table.judgment_rows)
        for number, row in table.rows:
            state = review_table_format.response_state(row)
            level = row[_LEVEL] or "未記録"
            for counts in (summary, summary["by_level"][level], summary["by_model"][model]):
                counts[state] += 1
            if state == "rejected":
                reason = review_table_format.rejection_reason_class(row[_NO_RESPONSE])
                summary["by_reason"][reason] += 1
                rejections.append(
                    {
                        "kind": "review-adoption-rejection",
                        "period": period,
                        "table": str(table.path),
                        "row": number,
                        "level": level,
                        "model": model,
                        "reason_class": reason,
                        "location": row[_LOCATION],
                    }
                )
    summary["by_level"] = dict(sorted(summary["by_level"].items()))
    summary["by_model"] = dict(sorted(summary["by_model"].items()))
    summary["by_reason"] = dict(sorted(summary["by_reason"].items()))
    return [summary, *rejections]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, metavar="DATE", help="今回の期間の開始日時（必須）。時差なしはローカル時刻。")
    parser.add_argument("--until", metavar="DATE", help="今回の期間の終了日時。省略時は実行時刻。")
    parser.add_argument(
        "--private-notes",
        type=Path,
        metavar="DIR",
        help="private-notesのGit作業ツリー。省略時はatk config get private_notesと同じ解決。",
    )
    parser.add_argument(
        "--claude-projects-dir", type=Path, metavar="DIR", help="Claude記録の所在。省略時は~/.claude/projects。"
    )
    parser.add_argument(
        "--codex-sessions-dir", type=Path, metavar="DIR", help="Codex記録の所在。省略時はCodexの通常記録とアーカイブ。"
    )
    args = parser.parse_args(argv)
    try:
        since = _parse_cli_timestamp(args.since)
        until = _parse_cli_timestamp(args.until) if args.until else datetime.datetime.now().astimezone()
        if since >= until:
            raise ValueError("開始が終了以後である")
        previous_since = since - (until - since)
        notes = (args.private_notes or private_notes.default_private_notes()).expanduser().resolve()
        history = _history(notes)
        current = saved_review_tables(notes, history, since, until)
        previous = saved_review_tables(notes, history, previous_since, since - datetime.timedelta(microseconds=1))
        codex_roots = (
            (args.codex_sessions_dir.expanduser(),)
            if args.codex_sessions_dir is not None
            else session_record_paths.codex_record_roots(host_homes.codex_home())
        )
        roots = [(args.claude_projects_dir or Path.home() / ".claude" / "projects").expanduser(), *codex_roots]
        tables = (*current.tables, *previous.tables)
        rows_by_table: dict[str, list[str]] = collections.defaultdict(list)
        for table in tables:
            rows_by_table[table.path.name].extend(_issue_key(review_table_format.issue_content(row)) for _, row in table.rows)
        models = _table_models([table.path.name for table in tables], roots, rows_by_table)
    except (ValueError, OSError) as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "text": str(error),
                    "next_action": "次の操作: 日時、private-notesとセッション記録の所在、Git履歴の読取権限を確認して再実行する",
                },
                ensure_ascii=False,
            )
        )
        return 2
    events = [
        *_period_events("previous", previous_since, since, previous.tables, models),
        *_period_events("current", since, until, current.tables, models),
        {
            "kind": "review-adoption-coverage",
            "unreadable_table_count": current.unreadable,
            "tables_without_history_count": current.without_history,
            "record_roots": [str(root) for root in roots],
            "model_unknown_note": "記録が残っていない表と実行レビュー担当を特定できない表はモデル不明として数える",
        },
    ]
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
