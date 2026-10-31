"""証拠抽出の`--tool-calls`照会。ツール呼び出しと入力・結果の対応を返す。"""

from __future__ import annotations

import collections
import datetime
import json
import pathlib
import re
import shlex
from typing import Any, NamedTuple

import session_evidence_execution
import session_evidence_static
from session_evidence_detail import _tool_result_body
from session_evidence_extract import (
    _HOOK_NOTICE_MARKER,
    _codex_text_blocks,
    _CollectedRecord,
    _json_object,
    _launch_mode,
    _parse_timestamp,
    _Record,
    _role_document,
    _text_blocks,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_stats import (
    _claude_call_hint,
    _codex_call_hint,
    _stats_summary_data,
)

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._common import agy_records, requirement_units, shell_segments, shell_tokens

_APPLY_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_SHELL_ABSOLUTE_PATH = re.compile(
    r"""(?P<quote>['"])(?P<quoted>(?:/|[A-Za-z]:[\\/]|\\\\)[^'"]*)(?P=quote)|"""
    r"""(?<![\w:/\\])(?P<bare>(?:/|[A-Za-z]:[\\/]|\\\\)[^\s"'`;&|()<>{}\[\]]+)"""
)

_LARGE_OUTPUT_MIN_BYTES = 8_192


def _string_values(value: Any) -> list[str]:
    """JSONの値にある本文を取り出し、識別子・数値の置換は行わない。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in _string_values(child)]
    return []


def _input_component(name: str, inputs: dict[str, Any]) -> str:
    """生成した入力の用途を、公開ツール名と入力フィールドから分類する。"""
    if name in {"Write", "Edit", "MultiEdit", "apply_patch", "write", "edit", "patch"}:
        return "editing"
    if any(key in inputs for key in ("prompt", "message", "instructions")):
        return "delegation"
    if any(key in inputs for key in ("command", "cmd", "CommandLine")):
        return "command"
    return "other"


def _partial_repetitions(item: _CollectedRecord) -> list[dict[str, Any]]:
    """同じ記録へ新たに追加された文の一致位置を示す。APIの履歴再送量は数えない。

    既存の要求単位抽出を使い、短い定型句も意味判断用の候補として残す。
    圧縮・別担当への必要な配送と不要な再取込の区別は読者が前後関係から判断する。
    """
    units: dict[str, list[dict[str, Any]]] = {}
    events = _conversation_tool_calls(item.records)
    for record in item.records:
        message = record.entry.get("message", record.entry.get("payload"))
        if isinstance(message, dict):
            extractor = _codex_text_blocks if item.runtime == "codex" else _text_blocks
            for text in extractor(message.get("content")):
                events.append({"text": text, "line": record.line, "kind": "message"})
    for event in sorted(events, key=lambda event: event["line"]):
        text = event["text"]
        try:
            value = json.loads(text)
        except ValueError, TypeError:
            value = text
        for body_number, body in enumerate(_string_values(value), 1):
            # フェンス内の引用・JSON文字列も比較へ含める。分割自体は共通実装を使う。
            body = re.sub(r"^\s*```[^\n]*$", "", body, flags=re.MULTILINE)
            for unit_number, unit in enumerate(requirement_units.requirement_units(body.splitlines()), 1):
                row = {
                    "location": f"{item.record_id}:{event['line']}",
                    "component": event["kind"],
                    "body": body_number,
                    "unit": unit_number,
                }
                locations = units.setdefault(unit, [])
                if row not in locations:
                    locations.append(row)
    return [
        {"excerpt": unit[:120], "characters": len(unit), "locations": locations, "count": len(locations)}
        for unit, locations in units.items()
        if len(locations) > 1
    ]


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
    if not isinstance(command, str) or any(operator in command for operator in ";&|<>"):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False
    executable = pathlib.PurePosixPath(argv[0]).name
    return executable == "cat" or shell_tokens.is_atk_command(argv) and argv[1:2] == ["read-file"]


def _conversation_tool_calls(records: list[_Record]) -> list[dict[str, Any]]:
    """記録上の全呼び出しと結果を、入力全文・識別子・位置の対応を保って返す。"""
    entries = {record.line: record.entry for record in records}
    events = []
    for record in records:
        entry = record.entry
        step = entry.get("step_update")
        if entry.get("event") != "step_update" or not isinstance(step, dict):
            continue
        info = step.get("tool_info")
        if step.get("step_type") != "tool" or step.get("state") == "ACTIVE" or not isinstance(info, dict):
            continue
        common = {
            "record": "main",
            "line": record.line,
            "timestamp": entry.get("timestamp"),
            "call_id": None,
            "tool": str(step.get("tool_name", "")),
        }
        events.extend(
            [
                {**common, "kind": "tool-call", "text": json.dumps(info.get("parameters"), ensure_ascii=False)},
                {**common, "kind": "tool-result", "text": json.dumps(info, ensure_ascii=False)},
            ]
        )
    for call in _record_tool_calls(records):
        inputs = _recorded_input(entries[call.line], call.call_id)
        payload = entries[call.line].get("payload")
        raw_input = payload.get("input") if isinstance(payload, dict) else None
        events.append(
            {
                "kind": "tool-call",
                "record": "main",
                "line": call.line,
                "timestamp": call.timestamp,
                "tool": call.tool,
                "call_id": call.call_id,
                "text": json.dumps(inputs, ensure_ascii=False)
                if inputs
                else raw_input
                if isinstance(raw_input, str)
                else call.text,
                "result_line": call.result_line,
            }
        )
        if call.result_line is not None:
            result = _recorded_result(entries[call.result_line], call.call_id)
            if result is not None:
                events.append(
                    {
                        "kind": "tool-result",
                        "record": "main",
                        "line": call.result_line,
                        "timestamp": entries[call.result_line].get("timestamp"),
                        "call_id": call.call_id,
                        "tool": call.tool,
                        "text": result,
                        "input_line": call.line,
                    }
                )
    return events


def comparison_material_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """正常な委譲・規範読込も比較できる事実と全文位置を、欠陥の採点をせず返す。"""
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        for call in _record_tool_calls(item.records):
            name = call.tool.rsplit("__", 1)[-1]
            delegate = name in {"Agent", "Task", "spawn_agent", "followup_task", "send_message"} or (
                "agents_server" in call.tool and name in {"start", "send_message"}
            )
            explicit_read = name in {"Read", "read_file", "Skill", "read", "skill"}
            opaque = name in {"Bash", "bash", "shell", "exec", "exec_command", "functions.exec", "functions.exec_command"}
            if not (delegate or explicit_read or opaque):
                continue
            inputs = _recorded_input(entries[call.line], call.call_id)
            body = next(
                (inputs[key] for key in ("prompt", "message", "instructions") if isinstance(inputs.get(key), str)),
                None,
            )
            paths = {
                key: value
                for key, value in inputs.items()
                if isinstance(value, str)
                and key != "subagent_md_path"
                and (key.lower().endswith("file") or key.lower().endswith("path"))
            }
            named_inputs = inputs.get("extra_params")
            if isinstance(named_inputs, dict):
                paths.update(
                    (f"extra_params.{key}", value)
                    for key, value in named_inputs.items()
                    if isinstance(value, str)
                    and (pathlib.PurePosixPath(value).is_absolute() or pathlib.PureWindowsPath(value).is_absolute())
                )
            result = _recorded_result(entries[call.result_line], call.call_id) if call.result_line is not None else None
            event = {
                "kind": "comparison-material",
                "record": item.record_id,
                "role": item.role,
                "line": call.line,
                "result_line": call.result_line,
                "timestamp": call.timestamp,
                "tool": call.tool,
                "category": "delegation" if delegate else "explicit-read" if explicit_read else "opaque-command",
                "target": paths or ({"skill": inputs["skill"]} if isinstance(inputs.get("skill"), str) else {}),
                "input_form": "file-reference" if delegate and paths else "direct-body" if body is not None else "command",
                "observed_body_characters": len(body) if body is not None else None,
                "recorded_result_characters": len(result) if result is not None else None,
                "recorded_result_utf8_bytes": len(result.encode("utf-8")) if result is not None else None,
                "read_range": {key: inputs[key] for key in ("offset", "limit", "start", "max_bytes") if key in inputs},
                "summary": call.text,
            }
            events.append(event)
    return events


def resource_usage_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """担当ごとの読込・Skill・反復・結果量を、欠陥の判定を加えず集計する。

    shellとJavaScriptの入力は命令文字列のまま残し、ファイルの読了件数へ換算しない。
    結果上位は各担当から選ぶため、非律速の担当の大量出力にも到達できる。
    """
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        calls = _record_tool_calls(item.records)
        reads: list[dict[str, Any]] = []
        skills: list[dict[str, Any]] = []
        shell_paths: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        large_reviews: list[dict[str, Any]] = []
        generated_inputs: list[dict[str, Any]] = []
        hook_notices: dict[tuple[str, str, str], dict[str, Any]] = {}
        expected_large_reads = 0
        repeats: dict[tuple[str, str], list[str]] = {}
        for call in calls:
            name = call.tool.rsplit("__", 1)[-1]
            inputs = _recorded_input(entries[call.line], call.call_id)
            locator = f"{item.record_id}:{call.line}"
            input_text = json.dumps(inputs, ensure_ascii=False) if inputs else call.text
            generated_inputs.append(
                {
                    "location": locator,
                    "component": _input_component(name, inputs),
                    "utf8_bytes": len(input_text.encode("utf-8")),
                    "managed_temp_paths": [
                        match["quoted"] or match["bare"]
                        for match in _SHELL_ABSOLUTE_PATH.finditer(input_text)
                        if "managed-temp/" in (match["quoted"] or match["bare"])
                    ],
                }
            )
            result = _recorded_result(entries[call.result_line], call.call_id) if call.result_line is not None else None
            result_metrics = {
                "result_location": f"{item.record_id}:{call.result_line}" if call.result_line is not None else None,
                "characters": len(result) if result is not None else None,
                "utf8_bytes": len(result.encode("utf-8")) if result is not None else None,
            }
            if call.text:
                repeats.setdefault((call.tool, call.text), []).append(locator)
            if name in {"Read", "read_file", "Skill", "read", "skill"}:
                row = {
                    "location": locator,
                    "tool": call.tool,
                    "target": inputs.get("skill", inputs.get("name"))
                    if name in {"Skill", "skill"}
                    else inputs.get("file_path", inputs.get("filePath", inputs.get("path", inputs.get("Path")))),
                    "range": {key: inputs[key] for key in ("offset", "limit", "start", "max_bytes") if key in inputs},
                    **result_metrics,
                }
                (skills if name in {"Skill", "skill"} else reads).append(row)
            if name in {"Bash", "bash", "shell", "exec_command", "functions.exec_command", "CommandExecution"}:
                command = inputs.get("command", inputs.get("cmd", call.text))
                if isinstance(command, str):
                    shell_paths.extend(
                        {"location": locator, "tool": call.tool, "target": match["quoted"] or match["bare"]}
                        for match in _SHELL_ABSOLUTE_PATH.finditer(command)
                    )
            if result is not None:
                for match in _HOOK_NOTICE_MARKER.finditer(result):
                    source = match["hook_xml"] or match["hook_legacy"] or "unknown"
                    kind = match["tag_xml"] or match["tag_legacy"] or "unknown"
                    end = result.find("</atk-auto>", match.end())
                    body = result[match.end() : end if end >= 0 else len(result)]
                    notice = hook_notices.setdefault(
                        (source, kind, body),
                        {"source": source, "notice_kind": kind, "excerpt": body[:120], "locations": []},
                    )
                    notice["locations"].append(f"{item.record_id}:{call.result_line}")
                result_row: dict[str, Any] = {
                    "location": locator,
                    "result_location": f"{item.record_id}:{call.result_line}",
                    "tool": call.tool,
                    "characters": len(result),
                    "utf8_bytes": len(result.encode("utf-8")),
                }
                results.append(result_row)
                if result_row["utf8_bytes"] >= _LARGE_OUTPUT_MIN_BYTES:
                    if _expected_body_read(call.tool, inputs):
                        expected_large_reads += 1
                    else:
                        large_reviews.append({**result_row, "input_summary": " ".join(call.text.split())[:300]})
        events.append(
            {
                "kind": "stats-resource-usage",
                "record": item.record_id,
                "role": item.role,
                "mode": _launch_mode(item, collected),
                "observed_identities": _stats_summary_data(item.records, item.runtime).get("observed_identities", [])
                if item.runtime is not None
                else [],
                "tool_calls": len(calls),
                "generated_inputs": generated_inputs,
                "generated_input_utf8_bytes": sum(row["utf8_bytes"] for row in generated_inputs),
                "hook_notices": list(hook_notices.values()),
                "partial_repetitions": _partial_repetitions(item),
                "reads": reads,
                "skills": skills,
                "shell_paths": shell_paths,
                "recorded_result_characters": sum(row["characters"] for row in results),
                "recorded_result_utf8_bytes": sum(row["utf8_bytes"] for row in results),
                "largest_results": sorted(results, key=lambda row: (-row["utf8_bytes"], row["location"]))[:10],
                "omitted_largest_result_count": max(0, len(results) - 10),
                "large_output_reviews": sorted(large_reviews, key=lambda row: (-row["utf8_bytes"], row["location"]))[:10],
                "large_output_review_count": len(large_reviews),
                "expected_large_read_count": expected_large_reads,
                "repeats": [
                    {"tool": tool, "input": text, "locations": locations, "count": len(locations)}
                    for (tool, text), locations in sorted(repeats.items(), key=lambda row: (-len(row[1]), row[0]))
                    if len(locations) > 1
                ][:10],
                "omitted_repeat_count": max(0, sum(len(locations) > 1 for locations in repeats.values()) - 10),
            }
        )
    return [
        *events,
        {
            "kind": "stats-resource-summary",
            "reads_by_target": _resource_target_groups(events, "reads"),
            "skills_by_name": _resource_target_groups(events, "skills"),
            "shell_paths_by_target": _resource_target_groups(events, "shell_paths"),
        },
    ]


def _resource_target_groups(events: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """同じ明示対象を担当横断でまとめ、担当内の回数と観測できた結果量を保持する。"""
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        for row in event[field]:
            target = row["target"]
            if not isinstance(target, str):
                continue
            group = groups.setdefault(
                target,
                {"target": target, "count": 0, "observed_result_count": 0, "characters": 0, "utf8_bytes": 0, "by_record": {}},
            )
            record = group["by_record"].setdefault(
                event["record"],
                {"count": 0, "observed_result_count": 0, "characters": 0, "utf8_bytes": 0, "locations": []},
            )
            group["count"] += 1
            record["count"] += 1
            record["locations"].append(row["location"])
            if row.get("characters") is not None:
                group["observed_result_count"] += 1
                group["characters"] += row["characters"]
                group["utf8_bytes"] += row["utf8_bytes"]
                record["observed_result_count"] += 1
                record["characters"] += row["characters"]
                record["utf8_bytes"] += row["utf8_bytes"]
    return sorted(groups.values(), key=lambda group: (-group["count"], group["target"]))


def _recorded_input(entry: dict[str, Any], call_id: str | None) -> dict[str, Any]:
    """明示されたツール入力だけを取得し、shellやJavaScriptの命令を読了の事実へ変換しない。"""
    message = entry.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == call_id:
                value = block.get("input")
                return value if isinstance(value, dict) else {}
    payload = entry.get("payload", {})
    if isinstance(payload, dict):
        raw = payload.get("arguments", payload.get("input"))
        item = payload.get("item")
        if raw is None and isinstance(item, dict) and item.get("type") == "McpToolCall":
            raw = item.get("arguments")
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except ValueError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _recorded_result(entry: dict[str, Any], call_id: str | None) -> str | None:
    """対応する結果本文の文字列を返し、取得量を現在のファイルサイズから推定しない。"""
    message = entry.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == call_id:
                return _tool_result_body(block, entry)
    payload = entry.get("payload", {})
    item = payload.get("item") if isinstance(payload, dict) else None
    if isinstance(item, dict) and item.get("type") in {"McpToolCall", "CommandExecution"}:
        return json.dumps(item.get("result", item), ensure_ascii=False)
    value = payload.get("output") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


class _ToolCall(NamedTuple):
    """記録にある1件のツール呼び出し。"""

    line: int
    timestamp: str | None
    tool: str
    call_id: str | None
    turn_id: str | None
    text: str
    """代表入力。`_claude_call_input`・`_codex_call_input`が返す値を切り詰めずに持つ。"""
    result_line: int | None
    """同じ呼び出し識別子を持つ最初の結果の記録行。結果の記録が無い場合は`None`。"""


def _record_turn_id(entry: dict[str, Any]) -> str | None:
    """Codexの外側セルと内側実行記録を比較するため、記録済みturnだけを返す。"""
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("turn_id")
    if not isinstance(value, str):
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        value = metadata.get("turn_id") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else None


def _record_tool_calls(records: list[_Record]) -> list[_ToolCall]:
    """1つの記録のツール呼び出しを、記録位置、時刻、ツール名、呼び出し識別子、代表入力および結果の記録行で返す。

    会話の流れ、`adhoc-processing`候補および`--tool-calls`が共有する抽出であり、
    Claude Code形式の`tool_use`要素とCodex形式の`function_call`・`custom_tool_call`のpayloadを1件の呼び出しとする。
    結果はClaude Code形式の`tool_result`の`tool_use_id`と、Codex形式の`function_call_output`・
    `custom_tool_call_output`の`call_id`で対応付ける。
    """
    records = [
        record._replace(entry=entry)
        for record, entry in zip(records, agy_records.normalize_entries([record.entry for record in records]), strict=True)
    ]
    pending: list[tuple[int, str | None, str, str | None, str | None, str]] = []
    result_lines: dict[str, list[int]] = {}
    active_turn_id: str | None = None
    for record in records:
        raw_timestamp = record.entry.get("timestamp")
        timestamp = raw_timestamp if isinstance(raw_timestamp, str) else None
        recorded_turn_id = _record_turn_id(record.entry)
        payload = record.entry.get("payload")
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        if payload_type == "task_started":
            # 新しいタスクの開始時点で前タスクの識別子を必ず破棄する。
            active_turn_id = recorded_turn_id
        turn_id = recorded_turn_id or active_turn_id
        message = record.entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = str(block.get("name", ""))
                    call_id = block.get("id")
                    pending.append(
                        (
                            record.line,
                            timestamp,
                            name,
                            call_id if isinstance(call_id, str) else None,
                            turn_id,
                            _claude_call_input(name, block.get("input")),
                        )
                    )
                elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    result_lines.setdefault(block["tool_use_id"], []).append(record.line)
            continue
        if not isinstance(payload, dict):
            continue
        call_id = payload.get("call_id")
        item = payload.get("item")
        if payload_type == "item_completed" and isinstance(item, dict) and item.get("type") == "CommandExecution":
            identity = item.get("id")
            if isinstance(identity, str) and any(call[3] == identity for call in pending):
                continue
            command = item.get("command", "")
            text = session_evidence_execution.command_execution_input(command)
            pending.append(
                (record.line, timestamp, "CommandExecution", str(identity) if identity is not None else None, turn_id, text)
            )
            if isinstance(identity, str):
                result_lines.setdefault(identity, []).append(record.line)
            continue
        if payload_type == "item_completed" and isinstance(item, dict) and item.get("type") == "McpToolCall":
            identity = item.get("id")
            if isinstance(identity, str) and any(call[3] == identity for call in pending):
                continue
            tool = f"mcp__{item.get('server')}__{item.get('tool')}"
            pending.append(
                (
                    record.line,
                    timestamp,
                    tool,
                    identity if isinstance(identity, str) else None,
                    turn_id,
                    json.dumps(item.get("arguments", {}), ensure_ascii=False),
                )
            )
            if isinstance(identity, str):
                result_lines.setdefault(identity, []).append(record.line)
            continue
        if payload_type in {"function_call", "custom_tool_call"}:
            name = str(payload.get("name", ""))
            pending.append(
                (
                    record.line,
                    timestamp,
                    name,
                    call_id if isinstance(call_id, str) else None,
                    turn_id,
                    _codex_call_input(name, payload),
                )
            )
        elif payload_type in {"function_call_output", "custom_tool_call_output"} and isinstance(call_id, str):
            result_lines.setdefault(call_id, []).append(record.line)
        if payload_type in {"task_complete", "turn_aborted"}:
            active_turn_id = None
    calls: list[_ToolCall] = []
    completed_lines = {
        record.line
        for record in records
        if isinstance(record.entry.get("payload"), dict) and record.entry["payload"].get("type") == "item_completed"
    }
    for line, timestamp, name, call_id, turn_id, text in pending:
        result_line = (
            next((value for value in result_lines.get(call_id, ()) if value >= line), None) if call_id is not None else None
        )
        if result_line is None and line in completed_lines:
            result_line = line
        calls.append(_ToolCall(line, timestamp, name, call_id, turn_id, text, result_line))
    return calls


def _claude_call_input(name: str, block_input: Any) -> str:
    """Claude Codeのツール呼び出しの代表入力を返す。`Write`・`Edit`は対象ファイルだけとし、本文を含めない。"""
    hint = _claude_call_hint(block_input)
    if hint is not None:
        return hint
    if not isinstance(block_input, dict):
        return ""
    keys = {"Agent": ("description", "prompt"), "Task": ("description", "prompt"), "Skill": ("skill", "args")}.get(name)
    if keys is not None:
        return " ".join(str(block_input[key]) for key in keys if isinstance(block_input.get(key), str) and block_input[key])
    return json.dumps(block_input, ensure_ascii=False, sort_keys=True)


def _codex_call_input(name: str, payload: dict[str, Any]) -> str:
    """Codexのツール呼び出しの代表入力を返す。パッチの適用は対象ファイルだけとし、差分の本文を含めない。"""
    raw = payload.get("input")
    if name == "apply_patch" and isinstance(raw, str):
        return " ".join(_APPLY_PATCH_FILE.findall(raw)) or name
    hint = _codex_call_hint(payload)
    if hint is not None:
        return hint
    arguments = payload.get("arguments")
    return arguments if isinstance(arguments, str) else raw if isinstance(raw, str) else ""


def _tool_call_events(item: _CollectedRecord, tools: list[str] | None, pattern: re.Pattern[str] | None) -> list[dict[str, Any]]:
    """1つの記録のツール呼び出しのうち、ツール名と代表入力の条件に一致するものを`tool-call`イベントで返す。

    本スクリプト自身の呼び出しも含める。`--grep`のように自己呼び出しを除くと、本スクリプトの呼び出し回数を数えられない。
    """
    return [
        {
            "kind": "tool-call",
            "record": item.record_id,
            "line": call.line,
            "timestamp": call.timestamp,
            "tool": call.tool,
            "call_id": call.call_id,
            "text": call.text,
            "result_line": call.result_line,
        }
        for call in _record_tool_calls(item.records)
        if (tools is None or call.tool in tools) and (pattern is None or pattern.search(call.text))
    ]


def _sorted_tool_call_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`tool-call`イベントを時刻の昇順に並べる。同時刻と時刻を持たないイベントは収集順と行番号の順を保つ。

    時刻を持たないイベントは最後に置く。入力のイベント列は記録の収集順と行番号の順に並んでいる前提とする。
    """
    timed: list[tuple[datetime.datetime, int, dict[str, Any]]] = []
    untimed: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        try:
            timestamp = _parse_timestamp(event["timestamp"]) if isinstance(event.get("timestamp"), str) else None
        except ValueError:
            timestamp = None
        if timestamp is None:
            untimed.append(event)
        else:
            timed.append((timestamp, index, event))
    timed.sort(key=lambda item: (item[0], item[1]))
    return [*(event for _, _, event in timed), *untimed]


def _tool_call_summary(events: list[dict[str, Any]], collected: list[_CollectedRecord] | None = None) -> dict[str, Any]:
    """`tool-call`イベントの件数と、ツール名ごと・記録ごとの件数を返す。"""
    summary = {
        "kind": "tool-call-summary",
        "count": len(events),
        "by_tool": dict(sorted(collections.Counter(str(event["tool"]) for event in events).items())),
        "by_record": dict(sorted(collections.Counter(str(event["record"]) for event in events).items())),
    }
    if collected is not None:
        groups: dict[str, dict[str, Any]] = {}
        owners = {item.record_id: item for item in collected}
        for event in events:
            owner = owners[str(event["record"])]
            document = _role_document(owner)
            key = f"document:{document}" if document else f"record:{owner.role}"
            group = groups.setdefault(key, {"count": 0, "by_tool": {}, "by_mode": {}})
            group["count"] += 1
            tool = str(event["tool"])
            group["by_tool"][tool] = group["by_tool"].get(tool, 0) + 1
            leaf = tool.rsplit("__", 1)[-1]
            if leaf in _agents_server_tool_names.RECORDED_START_OPERATIONS:
                inputs = _json_object(event["text"])
                if inputs is not None:
                    mode = _agents_server_tool_names.start_mode(leaf, inputs)
                    if mode is not None:
                        group["by_mode"][mode] = group["by_mode"].get(mode, 0) + 1
        summary["by_role"] = groups
    return summary


def _tool_call_collection_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    tools: list[str] | None,
    pattern: re.Pattern[str] | None,
) -> list[dict[str, Any]]:
    """単一transcriptのメイン記録と全ての委譲先の記録のツール呼び出しを時刻順に返し、末尾へ要約を置く。"""
    events = _sorted_tool_call_events([event for item in collected for event in _tool_call_events(item, tools, pattern)])
    return [*events, *_unresolved_events(unresolved), _tool_call_summary(events, collected)]
