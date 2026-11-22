from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import session_evidence_execution
import session_evidence_static
from agent_toolkit._common import shell_segments, shell_tokens
from session_evidence_extract import _CollectedRecord, _role_document
from session_evidence_tool_calls_records import _record_tool_calls, _recorded_input, _ToolCall


def _definition_read_targets(tool: str, inputs: dict[str, Any], text: str) -> list[str]:
    reads, _unresolved = _definition_read_operations(tool, inputs, text)
    return list(dict.fromkeys(target for row in reads for target in row["targets"]))


def _definition_read_operations(
    tool: str, inputs: dict[str, Any], text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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

_ROLE_DOCUMENT_DIRECTORIES = ("/share", "/skills/atk-mode/roles")
"""役割文書を置くディレクトリの末尾。移行前の記録は`share/`の役割文書を読むため、旧所在も含める。"""


def _may_name_definition(target: str) -> bool:
    """動的展開を含む読取対象が、`.subagent.md`で終わるパスになり得るかを返す。

    最後の動的展開より後ろの固定の末尾が`.subagent.md`で終わるか、`.subagent.md`の末尾部分（空、`.md`など）であれば
    読み得るとする。`$d/test-linux.log`のように固定の末尾から手順書になり得ない対象を未確定へ出さないためである。
    """
    if target.endswith(_SUBAGENT_SUFFIX):
        return True
    if "**" not in target:
        parts = target.split("/")
        patterned = [index for index, part in enumerate(parts) if any(char in part for char in "*?[")]
        if patterned:
            directory = parts[: patterned[-1]]
            static = "/".join(directory)
            if (
                directory
                and not any(char in static for char in _DYNAMIC_CHARS)
                and not f"/{static}".endswith(_ROLE_DOCUMENT_DIRECTORIES)
            ):
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


def _xargs_only_other_suffixes(value: str) -> bool:
    pipelines = shell_segments.extract_execution_pipelines(value, expand_shell=False)
    if len(pipelines) != 1 or len(pipelines[0]) != 2:
        return False
    producer, consumer = (segment.raw_tokens for segment in pipelines[0])
    if consumer not in {("xargs", "-0", "cat", "--"), ("xargs", "-0", "-r", "cat", "--")}:
        return False
    if producer[:2] != ("git", "diff") or "--" not in producer:
        return False
    end = producer.index("--")
    if set(producer[2:end]) != {"--name-only", "-z"}:
        return False
    pathspecs = producer[end + 1 :]
    return bool(pathspecs) and all(
        isinstance(item, shell_tokens.ShellWord)
        and not item.expansion
        and not item.unquoted
        and re.fullmatch(r"[*]\.[A-Za-z0-9]+", item)
        and not _may_name_definition(item)
        for item in pathspecs
    )


def _strip_env_prefix(words: list[str]) -> list[str] | None:
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
        if words[0] == "xargs" and _xargs_only_other_suffixes(value):
            return [], []
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
        rendered = " ".join(tokens)
        if _SUBAGENT_SUFFIX in rendered and re.search(r"read_text|readFile|open\s*\(|\.read\s*\(", rendered):
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
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        calls = _record_tool_calls(item.records)
        role_document = _role_document(item)
        related = []
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

            reads, unresolved = _definition_read_operations(call.tool, inputs, call.text)
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

    if any(word.redirection or set("()<>") & set(word.unquoted) for word in words):
        return False
    argv = list(words)
    if not argv:
        return False
    executable = pathlib.PurePosixPath(argv[0]).name
    return executable == "cat" or shell_tokens.is_atk_command(argv) and argv[1:2] == ["read-file"]
