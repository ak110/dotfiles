from __future__ import annotations

from typing import Any

from agent_toolkit._common import shell_quoting as _shell_quoting
from agent_toolkit._common import shell_segments as _shell_segments
from agent_toolkit._common import shell_tokens as _shell_tokens_module
from agent_toolkit._common.session_family import _json_object, _Record
from session_evidence_extract_common import _ENV_ASSIGNMENT, _PATH_SEPARATORS, _SCRIPT_RUNNERS, _SELF_SCRIPT_STEM, _SHELL_NAMES


def _scannable_records(records: list[_Record]) -> list[_Record]:
    self_call_ids: set[str] = set()
    scannable: list[_Record] = []
    for record in records:
        call_ids = _self_invocation_call_ids(record.entry)
        if call_ids is not None:
            self_call_ids |= call_ids
            continue
        if _result_call_ids(record.entry) & self_call_ids:
            continue
        scannable.append(record)
    return scannable


def _self_invocation_call_ids(entry: dict[str, Any]) -> set[str] | None:
    call_ids: set[str] = set()
    invoked = False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            block_input = block.get("input")
            command = block_input.get("command") if isinstance(block_input, dict) else None
            if not isinstance(command, str) or not _runs_self_script(command):
                continue
            invoked = True
            if isinstance(block.get("id"), str):
                call_ids.add(block["id"])

    payload = entry.get("payload")
    if isinstance(payload, dict) and _runs_self_script(_payload_command_input(payload)):
        invoked = True
        if isinstance(payload.get("call_id"), str):
            call_ids.add(payload["call_id"])
    return call_ids if invoked else None


def _runs_self_script(command: str | list[str]) -> bool:
    tokens = _shell_tokens(command) if isinstance(command, str) else command
    if not any(_SELF_SCRIPT_STEM in token for token in tokens):
        return False
    source = command if isinstance(command, str) else _shell_tokens_module.join_words(command)
    return any(_command_runs_self(segment) for segment in _command_segments(source))


def _command_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    scanner = _shell_quoting.QuotingScanner(command)
    start = 0
    while scanner.index < len(command):
        if scanner.consume_quoted():
            continue
        char = command[scanner.index]
        if char in "\"'":
            scanner.enter_quote(char)
            continue
        if char in ";&|\n":
            segments.append(_shell_tokens(command[start : scanner.index]))
            start = scanner.index + 1
        scanner.index += 1
    segments.append(_shell_tokens(command[start:]))
    return [segment for segment in segments if segment]


def _command_runs_self(tokens: list[str]) -> bool:
    index = 0
    while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
        index += 1
    words = tokens[index:]
    if not words:
        return False
    if _is_self_script(words[0]):
        return True
    name = _basename(words[0])
    if name in _SHELL_NAMES:
        # 記録されたパスの区切りを既存の実行名判定とそろえてから、起動引数を解析する。
        target = _shell_segments.shell_execution_target([name, *words[1:]])
        if target is None:
            return False
        kind, value = target
        return _runs_self_script(value) if kind == "command" else _is_self_script(value)
    if name in _SCRIPT_RUNNERS or name.startswith("python"):
        return any(_is_self_script(word) for word in words[1:])
    return False


def _is_self_script(token: str) -> bool:
    return _basename(token).startswith(_SELF_SCRIPT_STEM)


def _basename(token: str) -> str:
    return _PATH_SEPARATORS.split(token)[-1]


def _shell_tokens(command: str) -> list[str]:
    try:
        return list(_shell_tokens_module.split_words(command))
    except ValueError:
        return command.split()


def _payload_command_tokens(payload: dict[str, Any]) -> list[str]:
    """Codexの記録から、実行したコマンドのトークン列を取得する。

    カタログの操作集計は語の列を使い、自己呼出しの判定は改行を失わない元の入力を使う。
    """
    command = _payload_command_input(payload)
    return _shell_tokens(command) if isinstance(command, str) else command


def _payload_command_input(payload: dict[str, Any]) -> str | list[str]:
    """Codexの記録から、実行したコマンドの文字列またはargvを取得する。

    コマンドを保持するキー名は記録の種類で異なり、コマンド実行の呼び出し引数は`cmd`、
    完了項目は`command`を用いる。いずれの記録も同じ判定へ渡すため、両方のキーを探す。
    """
    item = payload.get("item")
    for source in (_json_object(payload.get("arguments")), item if isinstance(item, dict) else None):
        if source is None:
            continue
        for key in ("command", "cmd"):
            command = source.get(key)
            if isinstance(command, list):
                return [part for part in command if isinstance(part, str)]
            if isinstance(command, str):
                return command
    return []


def _result_call_ids(entry: dict[str, Any]) -> set[str]:
    call_ids: set[str] = set()
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        call_ids |= {
            block["tool_use_id"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)
        }

    payload = entry.get("payload")
    if (
        isinstance(payload, dict)
        and payload.get("type") in ("function_call_output", "custom_tool_call_output")
        and isinstance(payload.get("call_id"), str)
    ):
        call_ids.add(payload["call_id"])
    return call_ids


_RECORD_LOCATOR_NEXT_ACTION = (
    "`--detail`・`--record-schema`・`--context-at`へ`<記録ID>:<行番号>`の形で、"
    "オプションを指定しないときの出力が示す記録IDと行番号を渡して再実行する"
)
