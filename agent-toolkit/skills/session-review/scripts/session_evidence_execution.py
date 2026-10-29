"""記録されたセルからコマンド実行入力を選び、保存出力の渡し方を判定する。"""

import ast
import json
import pathlib
import re
from typing import NamedTuple

from agent_toolkit._common import heredocs, shell_segments, shell_tokens

_JS_TOKEN = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|\S",
    re.DOTALL,
)
_SAVED = re.compile(r"atk-output-|agents-wait-|/tool-results/|/tasks/[^\s'\"]*\.output(?!\w)")
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?$")
_PROCESSORS = frozenset({"jq", "awk", "gawk", "cut", "sed"})
SUBSTITUTION_MARK = "\ue000"
"""置換（`$(…)`・バッククォート・`<(…)`・`>(…)`）を外側の区間で置き換える動的な語。"""
_SYNTAX_WORDS = frozenset(
    {"{", "}", "(", ")", "!", "do", "then", "else", "elif", "if", "while", "until", "done", "fi", "esac", "time"}
)
"""単純コマンドの先頭で実行コマンドの位置を占めない構文語。"""


class SimpleCommand(NamedTuple):
    """置換の本体と複合コマンドの本体まで分けた単純コマンド1件。"""

    words: tuple[str, ...]
    """先頭の構文語を除いた語の列。`for`・`select`の見出しは先頭語を残して返す。"""
    source: str
    """単純コマンドを含むシェル文字列。置換の内側では置換の本体を指す。"""
    parsed: bool
    """区間の構文を解析できたか。偽の要素は語を持たない。"""


def _literal(token: str) -> str | None:
    """静的なJavaScript文字列だけを復号し、式の評価を行わない。"""
    if token.startswith("`"):
        if "${" in token:
            return None
        token = '"' + token[1:-1].replace('"', '\\"').replace("\n", "\\n") + '"'
    try:
        value = json.loads(token) if token.startswith('"') else ast.literal_eval(token)
    except ValueError, SyntaxError:
        return None
    return value if isinstance(value, str) else None


def command_inputs(tool: str, text: str) -> list[str]:
    """実行ツールの静的なcmd文字列を呼び出しごとに返す。説明用文字列は走査しない。"""
    return [row["command"] for row in command_input_observations(tool, text) if row["command"] is not None]


def command_input_observations(tool: str, text: str) -> list[dict[str, str | None]]:
    """実行入力と未確定理由を操作ごとに保つ。記録された式は評価しない。"""
    leaf = tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1].casefold()
    if leaf != "exec":
        return [{"command": text, "input": text, "reason": None}]
    matches = [match for match in _JS_TOKEN.finditer(text) if not match[0].startswith(("//", "/*"))]
    tokens = [match[0] for match in matches]
    commands = []
    for index in range(len(tokens) - 3):
        if tokens[index : index + 4] != ["tools", ".", "exec_command", "("]:
            continue
        end = index + 4
        parentheses = 1
        while end < len(tokens) and parentheses:
            if tokens[end] == "(":
                parentheses += 1
            elif tokens[end] == ")":
                parentheses -= 1
            end += 1
        source = text[matches[index].start() : matches[end - 1].end()]
        observation = {"command": None, "input": source, "reason": "実行引数を静的に確定できない"}
        commands.append(observation)
        if index + 4 >= len(tokens) or tokens[index + 4] != "{":
            continue
        depth = 1
        cursor = index + 5
        ambiguous = False
        command_count = 0
        while cursor < len(tokens) and depth:
            token = tokens[cursor]
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
            if depth == 1 and tokens[cursor : cursor + 3] == [".", ".", "."]:
                ambiguous = True
            # 計算キーとcmdの短縮記法・アクセサーは、読取に見える値を上書きし得る。
            # キーを求める式やgetterは評価せず、この操作の全体を未確定へ送る。
            if depth == 1 and token == "[":
                ambiguous = True
            if (
                depth == 1
                and (token == "cmd" or _literal(token) == "cmd")
                and tokens[cursor - 1] in {"{", ",", "get", "set"}
                and cursor + 1 < len(tokens)
                and tokens[cursor + 1] in {",", "}", "("}
            ):
                ambiguous = True
            if (
                depth == 1
                and (token == "cmd" or _literal(token) == "cmd")
                and cursor + 2 < len(tokens)
                and tokens[cursor + 1] == ":"
            ):
                value = _literal(tokens[cursor + 2])
                command_count += 1
                if command_count > 1:
                    ambiguous = True
                if value is not None and cursor + 3 < len(tokens) and tokens[cursor + 3] in {",", "}"}:
                    observation["command"] = value
                    observation["reason"] = None
            cursor += 1
        if cursor < len(tokens):
            observation["input"] = text[matches[index].start() : matches[cursor].end()]
        if depth or ambiguous:
            observation["command"] = None
            observation["reason"] = "実行引数の閉じ位置または上書きを静的に確定できない"
    return commands


def command_execution_input(command: object) -> str:
    """CommandExecutionのargvから、シェルが受け取った実行文字列だけを返す。"""
    if isinstance(command, list) and pathlib.PurePosixPath(str(command[0])).name in {"bash", "sh", "zsh"}:
        for index, argument in enumerate(command[:-1]):
            option = str(argument)
            if option.startswith("-") and "c" in option[1:]:
                return str(command[index + 1])
    return " ".join(map(str, command)) if isinstance(command, list) else str(command)


def command_file_inputs(name: str, args: tuple[str, ...]) -> list[str]:
    """加工ツールのオプションとプログラムを除いた明示入力を返す。"""
    values: list[str] = []
    expression = name in {"sed", "awk", "gawk", "jq", "rg", "grep"}
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--":
            values.extend(args[index + 1 + int(expression) :])
            return values
        if name in {"rg", "grep"} and value in {
            "-g",
            "--glob",
            "--iglob",
            "-t",
            "--type",
            "-T",
            "--type-not",
            "--encoding",
            "-m",
            "--max-count",
            "-A",
            "--after-context",
            "-B",
            "--before-context",
            "-C",
            "--context",
            "--include",
            "--exclude",
            "--exclude-dir",
            "--color",
            "--colors",
            "--sort",
            "--sortr",
        }:
            index += 2
            continue
        if name == "rg" and value == "-E":
            index += 2
            continue
        if name == "jq" and value in {"--arg", "--argjson", "--slurpfile", "--rawfile"}:
            index += 3
            continue
        if name == "jq" and value in {"--indent", "-L", "--library-path"}:
            index += 2
            continue
        if name == "jq" and value in {"--args", "--jsonargs"}:
            return []
        if value in {"-e", "--expression", "-f", "--file", "--from-file"} and expression:
            if name == "jq" and value == "-e":
                index += 1
                continue
            expression = False
            index += 2
            continue
        if value in {"-f", "-d", "--fields", "--delimiter"} and name == "cut":
            index += 2
            continue
        if (name in {"head", "tail"} and value in {"-n", "-c", "--lines", "--bytes"}) or (
            name in {"awk", "gawk"} and value in {"-F", "-v"}
        ):
            index += 2
            continue
        if value.startswith("-") and value != "-":
            index += 1
            continue
        if expression:
            expression = False
        else:
            values.append(value)
        index += 1
    return values


def processes_output(command: str) -> bool:
    """実行位置と接続した標準入力から、その場のコード実行と保存出力の加工を判定する。"""
    for pipeline in shell_segments.extract_execution_pipelines(command):
        saved_stream = False
        for segment in pipeline:
            if not segment.resolved or not segment.tokens:
                saved_stream = False
                continue
            tokens = segment.tokens
            words = shell_tokens.strip_redirections(tokens)
            if not words:
                continue
            name = pathlib.PurePosixPath(words[0]).name
            args = words[1:]
            python = _PYTHON.fullmatch(name) is not None
            if "/managed-temp/" in words[0] and name.endswith(".py"):
                return True
            if python and (
                "-c" in args
                or "<<" in " ".join(tokens)
                or any("/managed-temp/" in value and value.endswith(".py") for value in args)
            ):
                return True
            if name in {"node", "nodejs"} and any(value in {"-e", "--eval"} for value in args):
                return True
            redirected = []
            for index, token in enumerate(tokens):
                if token == "<" and index + 1 < len(tokens):
                    redirected.append(tokens[index + 1])
                elif token.startswith("<") and not token.startswith("<<"):
                    redirected.append(token[1:])
            inputs = command_file_inputs(name, args)
            explicit = [value for value in [*inputs, *redirected] if value != "-"]
            direct_saved = any(_SAVED.search(value) for value in explicit)
            if (name in _PROCESSORS or python) and (direct_saved or (saved_stream and not explicit)):
                return True
            saved_stream = direct_saved if explicit else saved_stream
            if name in {"printf", "echo"}:
                saved_stream = False
    return False


def _substitution_end(text: str, start: int, closing: str) -> int | None:
    """`start`から始まる置換の本体の終端（閉じ記号の位置）を、引用と入れ子の括弧を保って返す。"""
    depth = 0
    quote: str | None = None
    index = start
    while index < len(text):
        char = text[index]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            index += 1
        elif quote == '"':
            if char == '"':
                quote = None
            elif text.startswith("$(", index):
                end = _substitution_end(text, index + 2, ")")
                if end is None:
                    return None
                index = end
        elif char in "'\"":
            quote = char
        elif closing == "`":
            if char == "`":
                return index
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def mask_substitutions(command: str) -> tuple[str, list[str]] | None:
    """置換を`SUBSTITUTION_MARK`へ置き換えた文字列と、置換の本体の列を返す。閉じない引用・置換では`None`を返す。

    単一引用の内側は置換として扱わず、二重引用の内側の`$(…)`とバッククォートは置換として扱う。
    """
    masked: list[str] = []
    bodies: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            masked.append(char)
            quote = None if char == "'" else quote
            index += 1
            continue
        if char == "\\":
            masked.append(command[index : index + 2])
            index += 2
            continue
        opening = None
        if command.startswith("$(", index):
            opening = (index + 2, ")")
        elif char == "`":
            opening = (index + 1, "`")
        elif quote is None and char in "<>" and command.startswith("(", index + 1):
            opening = (index + 2, ")")
        if opening is not None:
            end = _substitution_end(command, *opening)
            if end is None:
                return None
            bodies.append(command[opening[0] : end])
            masked.append(SUBSTITUTION_MARK)
            index = end + 1
            continue
        if char == '"':
            quote = None if quote == '"' else '"'
        elif quote is None and char == "'":
            quote = "'"
        masked.append(char)
        index += 1
    return None if quote is not None else ("".join(masked), bodies)


def _strip_syntax_words(words: list[str]) -> list[str]:
    """単純コマンドの前後にある構文語、`case`の見出しと分岐パターン、括弧の付着を除く。"""
    while words:
        head = words[0]
        if head in _SYNTAX_WORDS:
            words = words[1:]
        elif head == "case":
            words = words[words.index("in") + 1 :] if "in" in words else []
        elif head.endswith(")") and "(" not in head:
            # `case`の分岐パターン（`*)`、`a|b)`など）は実行コマンドではない。
            words = words[1:]
        elif head.startswith("(") and len(head) > 1:
            words = [head.lstrip("("), *words[1:]]
        else:
            break
    while words and words[-1] in {"}", ")"}:
        words = words[:-1]
    if words and words[-1].endswith(")") and words[-1].count(")") > words[-1].count("("):
        words = [*words[:-1], words[-1].rstrip(")")]
    return [word for word in words if word]


def shell_simple_commands(command: str) -> list[SimpleCommand]:
    """シェル文字列を、置換の本体と複合コマンドの本体まで含めた単純コマンドへ分ける。

    置換の本体は外側のコマンドより先に実行されるため先に並べる。外側では置換を`SUBSTITUTION_MARK`の語へ置き換え、
    引用外の`&&`・`||`・`;`・`|`などの区切りで区間へ分け、先頭の構文語を除く。構文を解析できない区間は`parsed`が偽の要素で返す。
    `shell_segments`の公開挙動は変えずに、その区間と生のトークンだけを使う。
    """
    masked = mask_substitutions(heredocs.mask_heredoc_bodies(command))
    if masked is None:
        return [SimpleCommand((), command, False)]
    text, bodies = masked
    result = [simple for body in bodies for simple in shell_simple_commands(body)]
    for segment in shell_segments.extract_execution_segments(text):
        tokens = segment.raw_tokens or segment.tokens
        if not tokens:
            if not segment.resolved:
                result.append(SimpleCommand((), text, False))
            continue
        # 区間は引用外の区切りで分割済みである。引数の`';'`のような引用された区切り文字は語として残るため、
        # トークンで再分割せずに区間全体を1件の単純コマンドとして扱う。
        words = _strip_syntax_words(list(tokens))
        if words:
            result.append(SimpleCommand(tuple(words), text, True))
    return result
