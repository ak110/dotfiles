"""Codexの成功した読取を観測し、省略時は担当による補完を案内する。

hookは資料本文を追加コンテキストへ載せず、保存用の写しとの一致も判定しない。
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from agent_toolkit._common.shell_segments import split_bash_commands
from agent_toolkit._common.shell_tokens import ShellWord, split_words
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import formatter

_FILTER = re.compile(r"\.(?:[A-Za-z_]\w*)?(?:(?:\.[A-Za-z_]\w*)|(?:\[\d*\]))*")
# `sed -n`へ渡す印字だけのスクリプト。`;`区切りの印字指定を並べ、番地は行番号・`$`・静的な正規表現に限る。
# 正規表現には区切り・エスケープ・角括弧・`;`・改行を含めず、sedの解釈と番地の境界がずれる記述を受理しない。
# `p`以外のコマンド（書込の`w`、外部実行の`e`、置換の`s`など）は受理しない。
_SED_ADDRESS = r"(?:\d+|\$|/[^/\\\[;\n]+/)"
_SED_PRINT = re.compile(rf"{_SED_ADDRESS}(?:,{_SED_ADDRESS})?p(?:;{_SED_ADDRESS}(?:,{_SED_ADDRESS})?p)*")


@dataclass(frozen=True)
class Read:
    """展開と副作用を含まない標準読取のargvと入力ファイルの位置。"""

    argv: tuple[str, ...]
    operands: tuple[int, ...]


_GROUP_CONNECTORS = frozenset({";", "&&", "\n"})
"""読取計画が受理する連結。パイプ・`||`・背景実行は前段の出力や失敗を後段が扱うため受理しない。"""
_SUBSHELL_OR_REDIRECTION = frozenset("()<>")


def read_command_groups(command: str, *, sequential: bool = False, read_prefix: bool = False) -> list[list[str]] | None:
    """読取計画と取得案内で同じ演算子の境界を使う。

    区間と区切りは`shell_segments.split_bash_commands`、語は`shell_tokens.split_words`の共通表現から得る。
    引用した`;`・`|`・`>`などはデータとして扱い、実際の区切りとリダイレクトだけで判定する。
    `sequential`は`&&`連結を、`read_prefix`は`;`・改行の連結と展開を含む語を受理しない。
    `read_prefix`では先頭から確認可能な読取と前置きだけを残す。
    """
    groups: list[list[str]] = []
    for connector, segment in split_bash_commands(command):
        if connector and connector not in _GROUP_CONNECTORS:
            return None
        if sequential and connector == "&&":
            return None
        if read_prefix and connector in {";", "\n"}:
            return None
        try:
            words: list[ShellWord] = split_words(segment)
        except ValueError:
            return None
        if any(word.redirection or _SUBSHELL_OR_REDIRECTION & set(word.unquoted) for word in words):
            return None
        if read_prefix and any(word.expansion for word in words):
            return None
        if words:
            groups.append(list(words))
    if read_prefix:
        # 後続の構文まで検証してから、先頭から確認可能な読取と前置きだけを残す。
        # 途中で解析を打ち切ると、その後のセミコロンによる失敗隠蔽を見落とす。
        end = next(
            (
                index
                for index, group in enumerate(groups)
                if pathlib.PurePath(group[0]).name not in {"cat", "sed", "head", "tail", "jq", "cd", "wc", "ls"}
            ),
            len(groups),
        )
        return groups[:end]
    return groups


def read_plan(command: str) -> list[Read]:
    """展開・副作用・外部入力参照を含まない表示だけを受理する。"""
    if "\\\n" in command:
        return []
    groups = read_command_groups(command)
    if groups is None or any(isinstance(word, ShellWord) and word.expansion for group in groups for word in group):
        return []
    result = []
    for group in groups:
        name = pathlib.PurePath(group[0]).name
        if group[0] not in {name, f"/bin/{name}", f"/usr/bin/{name}"}:
            return []
        position = 1
        if name == "sed":
            if len(group) < 4 or group[1] != "-n" or not _SED_PRINT.fullmatch(group[2]):
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
