"""証拠抽出の問題候補（session_evidence_candidates_commands）。"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, NamedTuple

from session_evidence_extract import _ENV_ASSIGNMENT, _SHELL_NAMES, _basename, _json_object, _shell_tokens

from agent_toolkit._common.bash_invocations import extract_bash_invocations, is_read_predicate
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.shell_quoting import QuotingScanner as _QuotingScanner
from agent_toolkit._common.shell_tokens import is_atk_command as _is_atk_command

_SHELL_OPERATOR_CHARS = frozenset(";&|<>()\n")


_CHECK_COMMANDS = frozenset(
    {
        ("pyfltr", "run"),
        ("pytest", ""),
        ("make", "test"),
        ("atk", "plan-check"),
        ("atk", "exec-review-evidence-check"),
        ("atk", "validate"),
        ("gh", "watch"),
        ("wait_ci.py", ""),
        ("wait-ci", ""),
    }
)


_CD_PREFIX_SEPARATORS = frozenset({";", "&&"})


def _event_without_cd_prefix(event: dict[str, Any]) -> dict[str, Any]:
    """終了コードを帰属させるコマンドを求めるため、先頭の`cd <パス>;`か`cd <パス> &&`を外したイベントを返す。

    作業ディレクトリを移すだけの前置は、後続の単独のコマンドが返す終了コードと出力を変えない。
    除外判定（`_is_normal_atk_no_match`・`_is_normal_nonterminal_result`・`_is_normal_negative_result`・
    `_is_normal_negative_tool_failure`・`_is_check_detected`）は、このイベントを前置の無い単独のコマンドとして判定する。
    外すのは引数1語の`cd`の前置1つだけとし、オプション付きの`cd`、引数の無い`cd`、`|`や改行による前置、
    2つ目以降の前置は外さない。出力（Bashは`text`、`CommandExecution`は`diagnostic`）に`cd:`を含む行がある場合は、
    `cd`が失敗して後続のコマンドが意図した場所で動いていないため、前置を外さず記録どおりのイベントを返す。
    """
    output = event.get("diagnostic") if event.get("tool") == "CommandExecution" else event.get("text")
    if isinstance(output, str) and any("cd:" in line or "Set-Location" in line for line in output.splitlines()):
        return event
    event = _event_without_powershell_wrapper(event)
    result = dict(event)
    operation = _json_object(str(event.get("operation", "")))
    if operation is not None and isinstance(operation.get("command"), str):
        stripped = _command_without_cd_prefix(operation["command"])
        if stripped is not None:
            result["operation"] = json.dumps({**operation, "command": stripped}, ensure_ascii=False, sort_keys=True)
    for key in ("command", "command_full"):
        try:
            args = json.loads(str(event.get(key, "")))
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(args, list)
            or len(args) < 3
            or not all(isinstance(arg, str) for arg in args)
            or _basename(args[0]) not in _SHELL_NAMES
            or args[1] not in {"-c", "-lc"}
        ):
            continue
        stripped = _command_without_cd_prefix(args[2])
        if stripped is not None:
            result[key] = json.dumps([*args[:2], stripped, *args[3:]], ensure_ascii=False)
    return _event_without_run_command_wrapper(result)


def _event_with_command(event: dict[str, Any], args: list[str]) -> dict[str, Any]:
    """判定用の引数だけを差し替え、元イベントは呼び出し側に保持する。"""
    result = {**event, "command": json.dumps(args, ensure_ascii=False), "command_full": json.dumps(args, ensure_ascii=False)}
    operation = _json_object(str(event.get("operation", "")))
    if operation is not None:
        result["operation"] = json.dumps({**operation, "command": shlex.join(args)}, ensure_ascii=False)
    return result


def _event_without_powershell_wrapper(event: dict[str, Any]) -> dict[str, Any]:
    """確定できる単独PowerShellコマンドだけを既存分類へ渡す。"""
    args = _event_command_args(event)
    if args and _basename(args[0]) in _SHELL_NAMES and len(args) == 3 and args[1] in {"-c", "-lc"}:
        tokens = _shell_command_tokens(args[2])
        args = [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else []
    inner = _powershell_inner_args(args)
    return _event_with_command(event, inner) if inner else event


def _event_command_args(event: dict[str, Any]) -> list[str]:
    raw = event.get("command_full", event.get("command"))
    if isinstance(raw, str):
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return args if isinstance(args, list) and all(isinstance(arg, str) for arg in args) else []
    operation = _json_object(str(event.get("operation", "")))
    command = operation.get("command") if operation else None
    tokens = _shell_command_tokens(command) if isinstance(command, str) else None
    return [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else []


def _powershell_inner_args(args: list[str]) -> list[str] | None:
    """文字列Commandだけを解析する。PowerShell 7.5の引用規則を使い、展開や式は解釈しない。

    典拠: https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_quoting_rules
    """
    if not args or args[0].replace("\\", "/").rsplit("/", 1)[-1].casefold() not in {
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }:
        return None
    index = 1
    while index < len(args):
        option = args[index].casefold()
        if option == "-command":
            break
        if option in {"-noprofile", "-nologo", "-noninteractive", "-sta", "-mta"}:
            index += 1
        elif option in {"-executionpolicy", "-windowstyle", "-inputformat", "-outputformat"} and index + 1 < len(args):
            index += 2
        else:
            return None
    if index + 2 != len(args) or args[index + 1] == "-":
        return None
    tokens = _powershell_command_tokens(args[index + 1])
    if (
        tokens
        and len(tokens) >= 4
        and tokens[0] == _ShellToken("cd", False)
        and not tokens[1].operator
        and not tokens[1].value.startswith("-")
        and tokens[2] in {_ShellToken(";", True), _ShellToken("&&", True)}
    ):
        tokens = tokens[3:]
    return [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else None


def _powershell_command_tokens(command: str) -> list[_ShellToken] | None:
    """引用された語と演算子を分ける。確定できないPowerShell式は候補へ残す。"""
    tokens: list[_ShellToken] = []
    word: list[str] = []
    quote = ""
    started = False
    index = 0
    while index < len(command):
        char = command[index]
        following = command[index + 1] if index + 1 < len(command) else ""
        if quote and char == quote:
            if following == quote:
                word.append(char)
                index += 2
                continue
            quote = ""
        elif char == "`" and quote != "'":
            if not following or following in "\r\n":
                return None
            word.append(
                {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}.get(
                    following, following
                )
            )
            index += 2
            started = True
            continue
        elif char in "‘’“”" or char == "$" and quote != "'" or not quote and char in "{}()@#,":
            return None
        elif not quote and char in "'\"":
            quote = char
        elif not quote and char in ";|&<>\r\n":
            if started:
                tokens.append(_ShellToken("".join(word), False))
                word, started = [], False
            operator = char + following if following == char else char
            tokens.append(_ShellToken(operator, True))
            index += len(operator)
            continue
        elif not quote and char.isspace():
            if started:
                tokens.append(_ShellToken("".join(word), False))
                word, started = [], False
            index += 1
            continue
        else:
            word.append(char)
        started = True
        index += 1
    if quote:
        return None
    if started:
        tokens.append(_ShellToken("".join(word), False))
    return tokens


def _event_without_run_command_wrapper(event: dict[str, Any]) -> dict[str, Any]:
    """保存済み子結果の正常な包装だけを子コマンドへ帰属させる。"""
    args = _event_command_args(event)
    if args and _basename(args[0]) in _SHELL_NAMES and len(args) == 3 and args[1] in {"-c", "-lc"}:
        tokens = _shell_command_tokens(args[2])
        args = [token.value for token in tokens] if tokens and not any(token.operator for token in tokens) else []
    if len(args) < 4 or not _is_atk_command(args) or args[1] != "run-command" or "--" not in args[2:]:
        return event
    text = str(event.get("diagnostic") or event.get("text") or "")
    results = [_json_object(line) for line in split_lines(text) if line.startswith("{")]
    if len(results) != 1 or results[0] is None:
        return event
    result = results[0]
    child = result.get("argv")
    code = result.get("child_exit_code")
    if (
        not isinstance(child, list)
        or not child
        or not all(isinstance(arg, str) for arg in child)
        or not isinstance(code, int)
        or isinstance(code, bool)
        or code != _failure_exit_code(event)
        or result.get("timed_out") is not False
        or "signal" not in result
        or result["signal"] is not None
        or not isinstance(result.get("record_path"), str)
        or not result["record_path"]
        or any(
            not isinstance(result.get(key), int) or isinstance(result[key], bool) or result[key] < 0
            for key in ("stdout_bytes", "stderr_bytes")
        )
        or child != args[args.index("--") + 1 :]
    ):
        return event
    inner = _event_with_command(event, child)
    inner["exit_code"] = code
    # 検証の検出は出力を伴う。検索の否定では、子の診断が無いことをバイト数で保証する。
    if result["stdout_bytes"] or result["stderr_bytes"]:
        return inner if _is_check_detected(inner) else event
    inner["diagnostic"] = ""
    inner["text"] = f"Exit code {code}" if event.get("tool_name") == "Bash" else "CommandExecution failed"
    return inner


def _command_without_cd_prefix(command: str) -> str | None:
    """先頭の`cd <パス>;`か`cd <パス> &&`を外したコマンド文字列を返す。前置が無い場合は`None`を返す。

    残りの語は引用し直し、引用の外の演算子だけを演算子として残すため、連結の判定は元のコマンドと変わらない。
    """
    tokens = _shell_command_tokens(command)
    if tokens is None or len(tokens) < 4:
        return None
    first, target, separator, *rest = tokens
    if (
        first.operator
        or first.value != "cd"
        or target.operator
        or target.value.startswith("-")
        or not separator.operator
        or separator.value not in _CD_PREFIX_SEPARATORS
        or rest[0].operator
    ):
        return None
    return " ".join(token.value if token.operator else shlex.quote(token.value) for token in rest)


def _single_read_predicate(command: str, exit_code: int) -> bool:
    """リダイレクト付きの単独述語へ終了1を帰属させる。制御演算子とパイプは既存分類へ委ねる。"""
    tokens = _shell_command_tokens(command)
    if (
        exit_code != 1
        or tokens is None
        or any(
            token.operator and any(char in token.value for char in ";|()\n") or token.operator and token.value in {"&&", "&"}
            for token in tokens
        )
    ):
        return False
    invocations = extract_bash_invocations(command)
    return len(invocations) == 1 and invocations[0].arguments_known and is_read_predicate(invocations[0].segment.tokens)


class _ShellToken(NamedTuple):
    """シェルのコマンド文字列から得た語1件。`operator`は引用の外にある演算子であることを表す。"""

    value: str
    operator: bool


_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


def _shell_command_tokens(command: str) -> list[_ShellToken] | None:
    """シェルのコマンド文字列を、演算子を独立した要素とする語の列へ分解する。解釈できない場合は`None`を返す。

    引用とエスケープは`QuotingScanner`で判定し、引用の外にある演算子文字の連続だけを演算子とする。
    引用またはエスケープで渡された演算子形の文字はデータの語に残り、連結構造の判定に使わない。
    """
    scanner = _QuotingScanner(command)
    # 文字ごとの区分を集める。`None`は語の区切り、真偽値は引用の外の演算子文字かを表す。
    pieces: list[tuple[str, bool] | None] = []
    while scanner.index < len(command):
        char = command[scanner.index]
        quote_before = scanner.quote
        escaped_before = scanner.escaped
        if scanner.consume_quoted():
            if escaped_before:
                if quote_before == '"' and char not in _DOUBLE_QUOTE_ESCAPABLE:
                    pieces.append(("\\" + char, False))
                elif char != "\n":
                    pieces.append((char, False))
            elif scanner.escaped:
                pass
            elif quote_before is not None and scanner.quote is None:
                pieces.append(("", False))
            else:
                pieces.append((char, False))
            continue
        if char in {"'", '"'}:
            pieces.append(("", False))
            scanner.enter_quote(char)
            continue
        scanner.index += 1
        if char == "\n":
            pieces.append((char, True))
        elif char.isspace():
            pieces.append(None)
        elif char in _SHELL_OPERATOR_CHARS:
            pieces.append((char, True))
        elif char == "#" and (not pieces or pieces[-1] is None or pieces[-1][1]):
            break
        else:
            pieces.append((char, False))
    if scanner.quote is not None or scanner.escaped:
        return None
    tokens: list[_ShellToken] = []
    current: list[tuple[str, bool]] = []
    for piece in [*pieces, None]:
        operator = None if piece is None else piece[1]
        if current and operator != current[0][1]:
            tokens.append(_ShellToken("".join(text for text, _ in current), current[0][1]))
            current = []
        if piece is not None:
            current.append(piece)
    if not tokens:
        return None
    return tokens


def _is_negative_search_command(tokens: list[_ShellToken], exit_code: int) -> bool:
    """出力の無い非0終了が、検索の一致0件という正常な否定結果に当たるかを返す。

    `&&`で連結した全段が読取専用の述語であれば、終了コード1を正常な否定結果とする。
    それ以外の演算子はパイプ（`|`）だけを許し、パイプラインの終了コードを決める最終段で判定する。
    最終段が読取専用の述語で終了コード1、または最終段が検索を起動する`xargs`で終了コード123
    （起動したコマンドのいずれかが1から125で終わったことを表す）の場合を一致0件とする。
    """
    if _ShellToken("&&", True) in tokens:
        parts: list[list[str]] = [[]]
        for token in tokens:
            if token.operator and token.value == "&&":
                parts.append([])
            elif token.operator:
                return False
            else:
                parts[-1].append(token.value)
        return exit_code == 1 and all(part and is_read_predicate(part) for part in parts)
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token.operator:
            if token.value != "|":
                return False
            segments.append([])
        else:
            segments[-1].append(token.value)
    if any(not segment for segment in segments):
        return False
    last = segments[-1]
    if exit_code == 1:
        return is_read_predicate(last)
    if exit_code == 123 and Path(last[0]).name == "xargs":
        start = next(
            (index for index, token in enumerate(last[1:], start=1) if Path(token).name in {"rg", "grep", "git"}), None
        )
        return start is not None and is_read_predicate(last[start:])
    return False


def _failure_command_parts(event: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    """失敗した実行の包装を外し、実行ファイル、サブコマンド、表示用本文と引数を返す。"""
    raw = event.get("command_full", event.get("command"))
    command = _json_object(str(event.get("operation", "")))
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        args = parsed if isinstance(parsed, list) and all(isinstance(value, str) for value in parsed) else []
    else:
        value = command.get("command") if command is not None else None
        args = _shell_tokens(value) if isinstance(value, str) else []
    # Bashの引用と演算子は原文で保ち、直接渡されたargvでは引用してデータとして扱う。
    value = command.get("command") if command is not None else None
    display = value if not isinstance(raw, str) and isinstance(value, str) else shlex.join(args)
    shell_unwrapped = False
    for _ in range(5):
        while args and _ENV_ASSIGNMENT.match(args[0]):
            args = args[1:]
        if not args:
            break
        name = _basename(args[0])
        if name == "timeout" and len(args) >= 3:
            args = args[2:]
        elif name == "env":
            index = 1
            while index < len(args):
                word = args[index]
                if word == "--":
                    index += 1
                    break
                if word in {"-i", "--ignore-environment", "-"} or _ENV_ASSIGNMENT.match(word):
                    index += 1
                elif word in {"-u", "--unset", "-C", "--chdir"} and index + 1 < len(args):
                    index += 2
                elif (word.startswith(("-u", "-C")) and len(word) > 2) or word.startswith(("--unset=", "--chdir=")):
                    index += 1
                elif word.startswith("-"):
                    # 未解釈の包装は入力全体で区別し、オプション名による誤統合を避ける。
                    return "env", shlex.join(args), display, args
                else:
                    break
            args = args[index:]
        elif name in _SHELL_NAMES and len(args) >= 3 and args[1] in {"-c", "-lc"}:
            if not shell_unwrapped:
                display = args[2]
                shell_unwrapped = True
            args = _shell_tokens(args[2])
        elif (
            not any(
                "cd:" in line or "Set-Location" in line
                for line in str(event.get("diagnostic") or event.get("text") or "").splitlines()
            )
            and (inner := _powershell_inner_args(args)) is not None
        ):
            if not shell_unwrapped:
                display = args[-1]
                shell_unwrapped = True
            args = inner
        elif name == "uv" and len(args) >= 3 and args[1] == "run":
            index = 2
            while index < len(args) and args[index] in {"--frozen", "--locked", "--no-project"}:
                index += 1
            args = args[index:]
        else:
            break
    if not args:
        name = str(event.get("tool_name") or event.get("tool") or "")
        return name, "", display, []
    # 起動名の表記（絶対パス、atk.cmd、atk.pyなど）を共通の判定でatkへそろえ、後続の比較が1つの名前で済むようにする。
    name = "atk" if _is_atk_command(args) else _basename(args[0])
    if name == "git":
        index = 1
        while index < len(args):
            if args[index] in {"-C", "-c", "--git-dir", "--work-tree"} and index + 1 < len(args):
                index += 2
            elif args[index] == "--no-pager":
                index += 1
            else:
                break
        subcommand = args[index] if index < len(args) else ""
    elif (
        name == "atk"
        and len(args) >= 3
        and args[1] in {"run-script", "review-table"}
        or name == "gh"
        and len(args) >= 3
        and args[1] == "run"
    ):
        subcommand = args[2]
    elif name.startswith("python") or name in {"pytest", "wait_ci.py", "wait-ci"}:
        subcommand = ""
    else:
        subcommand = args[1] if len(args) >= 2 and not args[1].startswith("-") else ""
    return name, subcommand, display or " ".join(args), args


def _failure_exit_code(event: dict[str, Any]) -> int | None:
    value = event.get("exit_code")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = re.search(r"\bExit code (\d+)\b", str(event.get("text", "")))
    return int(match.group(1)) if match else None


def _is_check_detected(event: dict[str, Any]) -> bool:
    """検証コマンドが欠陥を検出して終了コード1を返した結果かを判定する。"""
    if _failure_exit_code(event) != 1:
        return False
    name, subcommand, _, args = _failure_command_parts(event)
    return (name, subcommand) in _CHECK_COMMANDS or "--check" in args
