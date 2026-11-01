"""Codexの成功した読取を観測し、省略時は担当による補完を案内する。

hookは資料本文を追加コンテキストへ載せず、保存用の写しとの一致も判定しない。
"""

from __future__ import annotations

import pathlib
import re
import shlex
from dataclasses import dataclass

from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import formatter

_FILTER = re.compile(r"\.(?:[A-Za-z_]\w*)?(?:(?:\.[A-Za-z_]\w*)|(?:\[\d*\]))*")


@dataclass(frozen=True)
class Read:
    """展開と副作用を含まない標準読取のargvと入力ファイルの位置。"""

    argv: tuple[str, ...]
    operands: tuple[int, ...]


def read_command_groups(command: str, *, sequential: bool = False, read_prefix: bool = False) -> list[list[str]] | None:
    """読取計画と取得案内で同じ演算子の境界を使う。"""
    lexer = shlex.shlex(command.strip() if read_prefix else command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    groups: list[list[str]] = [[]]
    conditional_chain = False
    try:
        for token in lexer:
            if read_prefix and token.rstrip("\n") in {";", "&&"}:
                token = token.rstrip("\n")
            if sequential and token == "&&":
                return None
            if token in {";", "&&", "\n"}:
                # &&の失敗を後続の独立コマンドが隠す場合は、短絡した読取を成功と推定できない。
                if read_prefix and conditional_chain and token != "&&" and groups[-1]:
                    return None
                conditional_chain = conditional_chain or token == "&&"
                if groups[-1]:
                    groups.append([])
            elif token and all(char in ";&|()<>\n" for char in token):
                return None
            else:
                # 診断などの後続処理を取得成功の条件にせず、先頭から直列に実行した読取だけを残す。
                if (
                    read_prefix
                    and not groups[-1]
                    and pathlib.PurePath(token).name not in {"cat", "sed", "head", "tail", "jq", "cd"}
                ):
                    break
                if (
                    read_prefix
                    and any(marker in token for marker in ("$", "`"))
                    and not (
                        len(groups[-1]) == 2
                        and pathlib.PurePath(groups[-1][0]).name == "sed"
                        and groups[-1][1] == "-n"
                        and re.fullmatch(r"(?:\d+|\$)(?:,(?:\d+|\$))?p", token)
                    )
                ):
                    return None
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


@dataclass(frozen=True)
class Observation:
    """読了観測へ渡す本文と、省略時だけ返す次の読取の案内。"""

    context: str
    body: str | None


# 省略を実行全体の限界と取り違え、分割取得で補える不足を理由に続行不能を返した委譲先が複数あったため、
# 省略が担当内で回収できる不足であることを次の操作とともに示す。
_RECOVERABLE = "省略は担当内で回収できる不足であり、続行できない理由にしない。"
TRUNCATED_NOTICE = (
    "この読取の出力は省略された。判断に必要な未取得部分を補え。"
    "保持可能な枠が元トークン数以上なら内側`exec_command`と外側`functions.exec`の予算を確保して1回再取得せよ。"
    "枠が不足または不明なら、`sed -n '<開始行>,<終了行>p' -- <絶対パス>`で行範囲を分けて必要箇所を読め"
    "（資料末尾も必要な場合は最後の範囲を`<開始行>,$p`とする）。" + _RECOVERABLE
)
TRUNCATED_OUTPUT_NOTICE = (
    "この出力は省略された。対象や条件を限定するか範囲を分けて再実行し、判断に必要な全量を回収せよ。" + _RECOVERABLE
)


def collect(payload: dict) -> Observation | None:
    """成功イベントの読取を観測し、省略は取得状態と独立して回復案内へ渡す。"""
    if not is_codex_payload(payload) or payload.get("hook_event_name", "PostToolUse") != "PostToolUse":
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    response = payload.get("tool_response")
    if (
        payload.get("tool_name") != "Bash"
        or not isinstance(command, str)
        or not isinstance(response, str)
        or not read_plan(command)
    ):
        return None
    if response_is_truncated(response):
        count = re.match(r"Warning: truncated output \(original token count: (\d+)\)", response)
        measured = f"観測した元の出力量は{count[1]}トークン。" if count else ""
        context = formatter("codex-read-observation")(measured + TRUNCATED_NOTICE)
        return Observation(context, response)
    return Observation("", response)
