"""証拠抽出のツール呼び出し（session_evidence_tool_calls_definitions）。"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import session_evidence_execution
import session_evidence_static
from session_evidence_extract import _CollectedRecord, _role_document
from session_evidence_tool_calls_records import _record_tool_calls, _recorded_input, _ToolCall

from agent_toolkit._common import shell_segments, shell_tokens


def _definition_read_targets(tool: str, inputs: dict[str, Any], text: str) -> list[str]:
    """静的に読取操作へ対応付けられた定義の対象だけを返す。"""
    reads, _unresolved = _definition_read_operations(tool, inputs, text)
    return list(dict.fromkeys(target for row in reads for target in row["targets"]))


def _definition_read_operations(
    tool: str, inputs: dict[str, Any], text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """共通の実行入力・区間境界から、明示読取と取得未確定の材料を分ける。"""
    name = tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    if name in {"Read", "read_file", "read"}:
        path = inputs.get("file_path", inputs.get("filePath", inputs.get("path", inputs.get("Path"))))
        direct_reads = [{"input": inputs, "targets": [path]}] if isinstance(path, str) and path.endswith(".subagent.md") else []
        return direct_reads, []
    if name.casefold() != "exec" and name not in {"Bash", "bash", "shell", "exec_command", "CommandExecution", "run_command"}:
        return [], []
    if name in {"Bash", "bash", "shell", "exec_command", "run_command"} and not any(
        key in inputs for key in ("command", "cmd", "CommandLine")
    ):
        return [], []
    command = inputs.get("command", inputs.get("cmd", inputs.get("CommandLine", text)))
    if not isinstance(command, str):
        return [], [{"input": inputs, "reason": "実行入力が文字列として記録されていない"}]
    reads: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for operation, observation in enumerate(session_evidence_execution.command_input_observations(tool, command), 1):
        value = observation["command"]
        if value is None:
            unresolved.append({"operation": operation, **observation})
            continue
        commands = session_evidence_execution.shell_simple_commands(value)
        values = {
            source: session_evidence_static.StaticValues(
                [simple.words for simple in commands if simple.source == source], source
            )
            for source in {simple.source for simple in commands}
        }
        for position, simple in enumerate(commands, 1):
            original: dict[str, Any] = {
                "operation": operation,
                "segment": position,
                "input": observation["input"],
                "command": value,
            }
            if not simple.parsed:
                unresolved.append({**original, "reason": "シェル区間の構文を静的に解析できない"})
                continue
            for words in values[simple.source].variants(simple.words):
                simple_reads, simple_unresolved = _simple_command_reads(list(words), value, original)
                reads.extend(simple_reads)
                unresolved.extend(simple_unresolved)
    return reads, unresolved


_DYNAMIC_CHARS = "$`*?[]{}~" + session_evidence_execution.SUBSTITUTION_MARK
"""読取対象やコマンド名を静的に確定できない展開を示す文字。"""


_SUBAGENT_SUFFIX = ".subagent.md"


def _may_name_definition(target: str) -> bool:
    """動的展開を含む読取対象が、`.subagent.md`で終わるパスになり得るかを返す。

    最後の動的展開より後ろの固定の末尾が`.subagent.md`で終わるか、`.subagent.md`の末尾部分（空、`.md`など）であれば
    読み得るとする。`$d/test-linux.log`のように固定の末尾から手順書になり得ない対象を未確定へ出さないためである。
    """
    if "**" not in target:
        parts = target.split("/")
        patterned = [index for index, part in enumerate(parts) if any(char in part for char in "*?[")]
        if patterned:
            directory = parts[: patterned[-1]]
            if directory and not any(char in "/".join(directory) for char in _DYNAMIC_CHARS) and directory[-1] != "share":
                return False
    tail_start = 0
    index = 0
    while index < len(target):
        char = target[index]
        if char == "$" and target.startswith("${", index):
            end = target.find("}", index + 2)
            index = len(target) if end < 0 else end + 1
            tail_start = index
            continue
        if char == "$":
            # 変数名（`$NAME`）か特殊変数（`$?`・`$1`など）までを展開とみなす。
            match = re.match(r"\$(?:[A-Za-z_]\w*|.)?", target[index:])
            assert match is not None
            index += len(match[0])
            tail_start = index
            continue
        index += 1
        if char in _DYNAMIC_CHARS:
            tail_start = index
    tail = target[tail_start:]
    return tail.endswith(_SUBAGENT_SUFFIX) or _SUBAGENT_SUFFIX.endswith(tail)


def _strip_env_prefix(words: list[str]) -> list[str] | None:
    """`env`の値の境界が既知のオプション（`-i`、`-u NAME`）と代入を除いた後続の語を返す。未知のオプションでは`None`を返す。"""
    index = 1
    while index < len(words):
        word = words[index]
        if word in {"-i", "-", "--ignore-environment"} or shell_segments.ENV_ASSIGN_PATTERN.match(word):
            index += 1
        elif word == "-u" and index + 1 < len(words):
            index += 2
        elif (word.startswith("-u") and len(word) > 2) or word.startswith("--unset="):
            index += 1
        elif word.startswith("-"):
            return None
        else:
            break
    return words[index:]


def _simple_command_reads(
    words: list[str], value: str, original: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """単純コマンド1件の手順書の読取と、読んだか静的に決められない材料を返す。

    実行コマンドを持たない区間（代入だけ、引数の無い`env`、`command -v`）、`for`・`select`の見出し、
    条件判定（`[`・`[[`）は本文を読まないため判定しない。
    """
    index = 0
    while index < len(words) and shell_segments.ENV_ASSIGN_PATTERN.match(words[index]):
        index += 1
    words = words[index:]
    if not words or words[0] in {"for", "select", "[", "[["}:
        return [], []
    if words[0] == "env":
        stripped = _strip_env_prefix(words)
        if stripped is None:
            if not any(pathlib.PurePosixPath(word).name == "xargs" or _may_name_definition(word) for word in words):
                return [], []
            return [], [{**original, "reason": "シェル区間の実行位置を静的に確定できない"}]
        words = stripped
        if not words:
            return [], []
    if words[0] == "command" and len(words) > 1 and words[1] in {"-v", "-V"}:
        return [], []
    segment = shell_segments.resolve_execution_segment(list(words))
    if not segment.resolved:
        if not any(pathlib.PurePosixPath(word).name == "xargs" or _may_name_definition(word) for word in words):
            return [], []
        return [], [{**original, "reason": "シェル区間の実行位置を静的に確定できない"}]
    tokens = shell_tokens.strip_redirections(segment.tokens)
    if not tokens:
        return [], []
    executable = pathlib.PurePosixPath(tokens[0]).name.casefold()
    args = tokens[1:]
    if any(char in tokens[0] for char in _DYNAMIC_CHARS):
        return [], [{**original, "reason": "実行するコマンドに動的展開がある"}]
    unresolved: list[dict[str, Any]] = []
    if shell_tokens.is_atk_command(tokens):
        if args[:1] != ("read-file",):
            return [], []
        args = args[1:]
        targets = list(args[args.index("--") + 1 :]) if "--" in args else []
        if not targets:
            unresolved.append({**original, "reason": "read-fileの対象境界を静的に確定できない"})
    elif executable in {"cat", "head", "tail", "more", "less", "sed", "awk", "gawk", "grep", "rg"}:
        if not session_evidence_execution.prints_file_body(executable, args):
            return [], []
        targets = session_evidence_execution.command_file_inputs(executable, args)
        for position, token in enumerate(segment.tokens):
            if token == "<" and position + 1 < len(segment.tokens):
                targets.append(segment.tokens[position + 1])
            elif token.startswith("<") and not token.startswith("<<"):
                targets.append(token[1:])
    elif executable == "get-content":
        targets, reason = session_evidence_execution.get_content_targets(args)
        if reason is not None:
            return [], [{**original, "reason": reason}]
    else:
        if _SUBAGENT_SUFFIX in value and re.search(r"read_text|readFile|open\s*\(|\.read\s*\(", " ".join(tokens)):
            unresolved.append({**original, "reason": "コード内の読取と対象の対応は静的抽出の対象外"})
        return [], unresolved
    static = []
    for target in targets:
        if any(char in target for char in _DYNAMIC_CHARS):
            if _may_name_definition(target):
                unresolved.append({**original, "target_input": target, "reason": "読取対象に動的展開がある"})
        elif target.endswith(_SUBAGENT_SUFFIX):
            static.append(target)
    return ([{**original, "targets": static}] if static else []), unresolved


def _inner_executions(call: _ToolCall, calls: list[_ToolCall]) -> tuple[list[_ToolCall] | None, str | None]:
    """完結した外側execの結果範囲内にある内側実行だけを返す。"""
    if (
        call.tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1].casefold() != "exec"
        or call.turn_id is None
        or not session_evidence_execution.command_input_observations(call.tool, call.text)
    ):
        return [], None
    if call.result_line is None:
        return None, "外側execの結果記録が無くセルの完結境界を確定できない"
    outers = [
        item
        for item in calls
        if item.turn_id == call.turn_id and item.tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1].casefold() == "exec"
    ]
    executions = [
        item
        for item in calls
        if item.turn_id == call.turn_id and item.tool == "CommandExecution" and call.line < item.line < call.result_line
    ]
    overlapping = [
        other
        for other in outers
        if other is not call and other.line < call.result_line and (other.result_line is None or call.line < other.result_line)
    ]
    if overlapping:
        return None, "同一turnの外側execの完結範囲が重なり内側実行の帰属が曖昧である"
    if not executions:
        return None, "外側execの範囲で完了済みCommandExecutionを確認できず、実行の完了状態が未確定"
    return executions, None


_START_ROLE = re.compile(r"""subagent_md_path["']?\s*:\s*["'`]([^"'`]+)["'`]""")


def _role_name(value: str) -> str:
    """役割名、`<役割名>.subagent.md`のファイル名、そのパスのいずれからも役割名を返す。"""
    return pathlib.PurePath(value).name.removesuffix(".md").removesuffix(".subagent")


def _launched_roles(call: _ToolCall, inputs: dict[str, Any], definitions: set[str]) -> set[str]:
    """呼び出しが起動する担当の役割名を返す。

    `agents_server`の`start`は`subagent_md_path`、Codexの`functions.exec`はセル本文の`start(`呼出しの同じ引数で判定する。
    `Agent`・`Task`・`spawn_agent`のように入力から役割を確定できない起動は、入力が手順書のファイル名を含めば
    その役割の起動とみなす（`definitions`は判定する手順書のファイル名の集合）。外側セル内の`spawn_agent(`も、
    前後の関連操作の抽出と同じく起動として扱い、セル本文に手順書のファイル名を含めば同じ規則を適用する。
    """
    name = call.tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    if name == "start":
        role = inputs.get("subagent_md_path")
        return {_role_name(role)} if isinstance(role, str) else set()
    if name.casefold() == "exec":
        roles = {_role_name(role) for role in _START_ROLE.findall(call.text)} if re.search(r"start\s*\(", call.text) else set()
        if re.search(r"spawn_agent\s*\(", call.text):
            roles |= {_role_name(definition) for definition in definitions if definition in call.text}
        return roles
    if name in {"Agent", "Task", "spawn_agent"}:
        text = json.dumps(inputs, ensure_ascii=False) if inputs else call.text
        return {_role_name(definition) for definition in definitions if definition in text}
    return set()


def _launch_check(
    call: _ToolCall, targets: list[str], role_document: str | None, launches: list[tuple[_ToolCall, set[str]]]
) -> dict[str, Any]:
    """手順書の読取が、起動準備の読込（別の担当を起動する前にその手順書を読むこと）に当たり得るかを判定する。

    同じ記録で読取より後に同じ役割を起動した読取だけを疑いあり（`suspected`）とし、後続の起動が無い読取と、
    読む主体の役割文書と同じ手順書の読取は判定済み（`cleared`）とする。起動の無い読取は禁止事項の対象外であり、
    自身の役割文書は受領した定義だからである。同じセル内の`start(`は前後を決められないため後続として扱う。
    """
    roles = {_role_name(target) for target in targets}
    if role_document is not None and roles <= {role_document}:
        return {"status": "cleared", "reason": "読む主体の役割文書と同じ手順書（自身が受領した定義）", "launch_lines": []}
    lines = [
        launch.line
        for launch, launched in launches
        if (launch.line > call.line or launch is call) and launched & (roles - {role_document})
    ]
    if lines:
        return {"status": "suspected", "reason": "読取の後に同じ役割を起動した", "launch_lines": lines}
    return {"status": "cleared", "reason": "読取の後に同じ役割の起動が無い", "launch_lines": []}


def definition_body_read_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """正常終了も含む定義本文の取得を、目的未判定の材料と前後の起動・編集の位置付きで返す。"""
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        calls = _record_tool_calls(item.records)
        role_document = _role_document(item)
        related = []
        associations = {call.line: _inner_executions(call, calls) for call in calls}
        linked_executions = {
            execution.line
            for executions, _reason in associations.values()
            if executions is not None
            for execution in executions
        }
        for call in calls:
            name = call.tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
            if name in {"Agent", "Task", "spawn_agent", "start", "Edit", "Write", "apply_patch"} or (
                name.casefold() == "exec" and re.search(r"(?:start|spawn_agent|apply_patch)\s*\(", call.text)
            ):
                related.append(call)
        definitions = {
            pathlib.PurePath(target).name
            for call in calls
            for target in _definition_read_targets(call.tool, _recorded_input(entries[call.line], call.call_id), call.text)
        }
        launches = [
            (call, roles)
            for call in calls
            if (roles := _launched_roles(call, _recorded_input(entries[call.line], call.call_id), definitions))
        ]
        for call in calls:
            inputs = _recorded_input(entries[call.line], call.call_id)
            executions, association_reason = associations[call.line]
            if executions is not None and executions:
                reads: list[dict[str, Any]] = []
                unresolved: list[dict[str, Any]] = []
                for execution in executions:
                    resolved, unresolved_rows = _definition_read_operations(
                        execution.tool, {"command": execution.text}, execution.text
                    )
                    for row in [*resolved, *unresolved_rows]:
                        row["execution_line"] = execution.line
                        row["execution_result_line"] = execution.result_line
                    reads.extend(resolved)
                    unresolved.extend(unresolved_rows)
            else:
                reads, unresolved = _definition_read_operations(call.tool, inputs, call.text)
                if unresolved and association_reason is not None:
                    # 入力だけでは読取の有無を決められない操作に、内側実行を使えない理由を付ける。
                    unresolved = [{"input": call.text, "reason": association_reason, "outer_turn_id": call.turn_id}]
            if call.tool == "CommandExecution" and call.line in linked_executions:
                continue
            if not reads and not unresolved:
                continue
            before = [other for other in related if other.line < call.line]
            after = [other for other in related if other.line > call.line]
            neighbors = [*(before[-1:]), *(after[:1])]
            targets = list(dict.fromkeys(target for row in reads for target in row["targets"]))
            for kind, operations in (("definition-body-read", reads), ("definition-read-unresolved", unresolved)):
                if not operations:
                    continue
                events.append(
                    {
                        "kind": kind,
                        "record": item.record_id,
                        "role": item.role,
                        "role_document": role_document,
                        "line": call.line,
                        "timestamp": call.timestamp,
                        "tool": call.tool,
                        "targets": targets if kind == "definition-body-read" else [],
                        "acquisition": "読取操作と対象を静的に対応付けた"
                        if kind == "definition-body-read"
                        else "取得の有無は未確定",
                        "operations": operations,
                        "recorded_input": inputs or call.text,
                        "read_range": {key: inputs[key] for key in ("offset", "limit", "start", "max_bytes") if key in inputs},
                        "text": call.text,
                        "result_line": call.result_line,
                        "result_observation": "記録あり・成否は原記録で判定"
                        if call.result_line is not None
                        else "結果記録なし",
                        "purpose": "未判定",
                        **(
                            {"launch_check": _launch_check(call, targets, role_document, launches)}
                            if kind == "definition-body-read"
                            else {}
                        ),
                        "related_operations": [
                            {"line": other.line, "tool": other.tool, "result_line": other.result_line} for other in neighbors
                        ],
                    }
                )
    return events


def _expected_body_read(tool: str, inputs: dict[str, Any]) -> bool:
    """本文取得と明示された単独操作だけを、大出力の点検上位から除く。"""
    name = tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    if name in {"Read", "read_file", "Skill", "read", "skill"}:
        return True
    if name not in {"Bash", "bash", "shell", "exec_command", "CommandExecution"}:
        return False
    command = inputs.get("command", inputs.get("cmd"))
    if not isinstance(command, str):
        return False
    commands = shell_segments.split_bash_commands(command)
    if len(commands) != 1:
        return False
    try:
        words = shell_tokens.split_words(commands[0][1])
    except ValueError:
        return False
    # 単独操作だけを除外する。引用の外側のリダイレクトやサブシェルを含む入力は除外しない。
    if any(word.redirection or set("()<>") & set(word.unquoted) for word in words):
        return False
    argv = list(words)
    if not argv:
        return False
    executable = pathlib.PurePosixPath(argv[0]).name
    return executable == "cat" or shell_tokens.is_atk_command(argv) and argv[1:2] == ["read-file"]
