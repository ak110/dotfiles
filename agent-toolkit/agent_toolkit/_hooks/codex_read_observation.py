"""Codexのnative読取の成功と省略の有無から、PostToolUseの読了観測に渡す本文を決める。

hookは資料本文を追加コンテキストへ載せない。省略された読取は再取得して補わず、
担当自身が行範囲を分けて末尾まで読む。取得の観測は、モデルへ実際に届いたnative本文だけを根拠にする。
省略補償を目的とした本文配送（2026年10月30日導入）は、ホスト本来の取得を代替して
二重配送と文脈消費を招いたため撤去した。経緯は`docs/development/incidents-runtime.md`
「フックとセッション状態の不全」の2026年10月19日の項にある。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shlex
from dataclasses import dataclass

from agent_toolkit._hooks.host import is_codex_payload

_FILTER = re.compile(r"\.(?:[A-Za-z_]\w*)?(?:(?:\.[A-Za-z_]\w*)|(?:\[\d*\]))*")


@dataclass(frozen=True)
class Read:
    """展開と副作用を含まない標準読取のargvと入力ファイルの位置。"""

    argv: tuple[str, ...]
    operands: tuple[int, ...]


def read_command_groups(command: str) -> list[list[str]] | None:
    """読取計画と取得案内で同じ演算子の境界を使う。"""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    groups: list[list[str]] = [[]]
    try:
        for token in lexer:
            if token in {";", "&&", "\n"}:
                if groups[-1]:
                    groups.append([])
            elif token and all(char in ";&|()<>\n" for char in token):
                return None
            else:
                groups[-1].append(token)
    except ValueError:
        return None
    return [group for group in groups if group]


def read_plan(command: str) -> list[Read]:
    """展開・副作用・外部入力参照を含まない表示だけを受理する。"""
    unquoted = re.sub(r"'[^']*'", "", command)
    if any(value in unquoted for value in ("$", "`", "\\\n")):
        return []
    groups = read_command_groups(command)
    if groups is None:
        return []
    result = []
    for group in groups:
        name = pathlib.PurePath(group[0]).name
        if group[0] not in {name, f"/bin/{name}", f"/usr/bin/{name}"}:
            return []
        position = 1
        if name == "sed":
            if len(group) < 4 or group[1] != "-n" or not re.fullmatch(r"(?:\d+|\$)(?:,(?:\d+|\$))?p", group[2]):
                return []
            position = 3
        elif name in {"head", "tail"}:
            if len(group) > 1 and group[1] == "-n":
                if len(group) < 4 or not group[2].isdigit():
                    return []
                position = 3
            elif len(group) > 1 and re.fullmatch(r"-n\d+", group[1]):
                position = 2
        elif name == "jq":
            while position < len(group) and group[position] in {"-r", "-c", "-e", "-s", "-S", "-M"}:
                position += 1
            if position >= len(group) or _FILTER.fullmatch(group[position]) is None:
                return []
            position += 1
        elif name != "cat":
            return []
        if position < len(group) and group[position] == "--":
            position += 1
        operands = tuple(range(position, len(group)))
        if not operands or any(group[index].startswith("-") for index in operands):
            return []
        # 複数ファイルのhead/tailはファイル名を見出しとして出力へ混ぜ、資料の本文と一致しなくなる。
        if name in {"head", "tail"} and len(operands) != 1:
            return []
        result.append(Read((name, *group[1:]), operands))
    return result


def response_is_truncated(response: object) -> bool:
    """nativeが付ける省略の包絡だけを識別し、本文中の語では判定しない。"""
    return isinstance(response, str) and response.startswith("Warning: truncated output (original token count:")


def _records(path: pathlib.Path):
    """長い記録は末尾から必要な行まで走査する。"""
    with path.open("rb") as stream:
        position = stream.seek(0, 2)
        pending = b""
        while position:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            lines = (stream.read(size) + pending).split(b"\n")
            pending = lines.pop(0) if position else b""
            for line in reversed(lines):
                try:
                    yield json.loads(line), hashlib.sha256(line).hexdigest()
                except ValueError:
                    continue


def _execution(payload: dict) -> dict | None:
    """同じturn・thread・操作の終了記録を返す。"""
    for record, _digest in _records(pathlib.Path(payload["transcript_path"])):
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            continue
        event = record.get("payload", {})
        if not isinstance(event, dict):
            continue
        item = event.get("item", {})
        item = item if isinstance(item, dict) else {}
        if (
            event.get("type") == "item_completed"
            and item.get("type") == "CommandExecution"
            and item.get("id") == payload.get("tool_use_id")
            and event.get("turn_id") == payload.get("turn_id")
            and event.get("thread_id") == payload.get("session_id")
        ):
            return item
    return None


def _succeeded(execution: dict, command: str) -> bool:
    """元の実行が同じコマンドを終了0で完了したかを返す。"""
    argv = execution.get("command", [])
    return (
        execution.get("exit_code") == 0
        and not isinstance(execution.get("exit_code"), bool)
        and execution.get("status") == "completed"
        and isinstance(argv, list)
        and len(argv) == 3
        and argv[-1] == command
    )


@dataclass(frozen=True)
class Observation:
    """読了観測へ渡す本文と、省略時だけ返す次の読取の案内。"""

    context: str
    body: str | None


# 省略を実行全体の限界と取り違え、分割取得で補える不足を理由に続行不能を返した委譲先が複数あったため、
# 省略が担当内で回収できる不足であることを次の操作とともに示す。
_RECOVERABLE = "省略は担当内で回収できる不足であり、続行できない理由にしない。"
TRUNCATED_NOTICE = (
    "この読取の出力は省略されたため全文は未取得。"
    "`sed -n '<開始行>,<終了行>p' -- <絶対パス>`で行範囲を分けて末尾まで読め（最後の範囲は`<開始行>,$p`）。" + _RECOVERABLE
)
TRUNCATED_OUTPUT_NOTICE = (
    "この出力は省略された。対象や条件を絞るか範囲を分けて再実行し、判断に必要な全量を回収せよ。" + _RECOVERABLE
)


def collect(payload: dict) -> Observation | None:
    """標準読取の終了記録と省略の包絡から、読了観測へ渡す本文を確定する。

    終了0で完了し省略の包絡が無いnative応答だけを本文として返す。
    省略された応答は本文を返さず、分割読取の案内だけを返す。
    """
    if (
        not is_codex_payload(payload)
        or payload.get("hook_event_name", "PostToolUse") != "PostToolUse"
        or payload.get("tool_name") != "Bash"
    ):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    response = payload.get("tool_response")
    if not isinstance(command, str) or not isinstance(response, str) or not read_plan(command):
        return None
    try:
        execution = _execution(payload)
    except OSError, UnicodeError, ValueError, KeyError, TypeError:
        return None
    if execution is None or not _succeeded(execution, command):
        return None
    if response_is_truncated(response):
        return Observation(TRUNCATED_NOTICE, None)
    return Observation("", response)
