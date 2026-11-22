"""記録されたセルからコマンド実行入力を選び、保存出力の渡し方を判定する。"""

import collections
import contextlib
import contextvars
import itertools
import json
import pathlib
import re
from collections.abc import Generator
from typing import Any, NamedTuple

import session_evidence_static
from agent_toolkit._common import heredocs, shell_segments, shell_tokens
from agent_toolkit._common.codex_execution_inputs import (
    command_input_observations,
    command_inputs,
    javascript_string,
    javascript_tokens,
)

__all__ = ["command_input_observations", "command_inputs"]

_SAVED = re.compile(r"atk-output-|agents-wait-|/tool-results/|/tasks/[^\s'\"]*\.output(?!\w)")
_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?$")
_PROCESSORS = frozenset({"jq", "awk", "gawk", "cut", "sed"})
SUBSTITUTION_MARK = session_evidence_static.SUBSTITUTION_MARK
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


_SHELL_PARSE_CACHE: contextvars.ContextVar[collections.OrderedDict[str, tuple[SimpleCommand, ...]] | None] = (
    contextvars.ContextVar("session_review_shell_parse_cache", default=None)
)


@contextlib.contextmanager
def shell_parse_scope() -> Generator[None]:
    """1回の準備・単独bundleの間だけ、不変な構文情報を最大128入力まで共有する。"""
    if _SHELL_PARSE_CACHE.get() is not None:
        yield
        return
    token = _SHELL_PARSE_CACHE.set(collections.OrderedDict())
    try:
        yield
    finally:
        _SHELL_PARSE_CACHE.reset(token)


def continuation_handles(tool: str, text: str) -> set[tuple[str, int | str]]:
    """後続回収の公開入力から実行IDとセルIDを区別し、静的な値だけを読む。"""
    keys = {"write_stdin": "session_id", "wait": "cell_id"}
    leaf = tool.rsplit("__", 1)[-1].rsplit(".", 1)[-1]
    values: list[tuple[str, Any]] = []
    if leaf in keys:
        try:
            inputs = json.loads(text)
        except ValueError:
            return set()
        if isinstance(inputs, dict):
            values.append((keys[leaf], inputs.get(keys[leaf])))
    elif leaf == "exec":
        matches = javascript_tokens(text)
        tokens = [match[0] for match in matches]
        for index in range(len(tokens) - 4):
            if tokens[index : index + 2] != ["tools", "."] or tokens[index + 2] not in keys:
                continue
            if tokens[index + 3 : index + 5] != ["(", "{"]:
                continue
            key = keys[tokens[index + 2]]
            cursor, depth, ambiguous = index + 5, 1, False
            found: list[Any] = []
            while cursor < len(tokens) and depth:
                token = tokens[cursor]
                if depth == 1 and (token == "[" or tokens[cursor : cursor + 3] == [".", ".", "."]):
                    ambiguous = True
                if depth == 1 and (token == key or javascript_string(token) == key) and tokens[cursor - 1] in {"{", ","}:
                    value = None
                    if cursor + 2 < len(tokens) and tokens[cursor + 1] == ":":
                        end = cursor + 2
                        while end < len(tokens) and tokens[end] not in {",", "}"}:
                            end += 1
                        if end > cursor + 2:
                            raw = text[matches[cursor + 2].start() : matches[end - 1].end()]
                            value = javascript_string(raw)
                            if value is None:
                                try:
                                    value = json.loads(raw)
                                except ValueError:
                                    value = None
                    found.append(value)
                depth += (token == "{") - (token == "}")
                cursor += 1
            if not depth and not ambiguous and len(found) == 1:
                values.append((key, found[0]))
    return {
        (key, value)
        for key, value in values
        if key == "session_id"
        and isinstance(value, int)
        and not isinstance(value, bool)
        or key == "cell_id"
        and isinstance(value, str)
    }


def command_execution_input(command: object) -> str:
    """CommandExecutionのargvから、シェルが受け取った実行文字列だけを返す。"""
    if isinstance(command, list):
        body = shell_segments.shell_c_argument([str(value) for value in command])
        if body is not None:
            return body
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
        if name == "sed" and (script_option := re.fullmatch(r"-[nErzsub]*[ef](.*)", value)):
            expression = False
            index += 1 if script_option[1] else 2
            continue
        if name == "jq" and value in {"--arg", "--argjson", "--slurpfile", "--rawfile"}:
            index += 3
            continue
        if name == "jq" and value in {"--indent", "-L", "--library-path"}:
            index += 2
            continue
        if name == "jq" and value in {"--args", "--jsonargs"}:
            return []
        pattern_options = (
            {"-e", "--regexp", "-f", "--file"}
            if name in {"rg", "grep"}
            else {"-e", "--expression", "-f", "--file"}
            if name == "sed"
            else {"-f", "--file", "--from-file"}
            if name in {"jq", "awk", "gawk"}
            else set()
        )
        if value.partition("=")[0] in pattern_options:
            expression = False
            index += 1 if "=" in value else 2
            continue
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


# rg・grepで、ファイル本文を出さずに一覧・件数・真偽だけを出すモードのオプション。
_LISTING_ONLY_OPTIONS = frozenset(
    {
        "--files",
        "-l",
        "-L",
        "--files-with-matches",
        "--files-without-match",
        "-c",
        "--count",
        "--count-matches",
        "-q",
        "--quiet",
    }
)
# Get-Contentで値を1つ取るオプション。
_GET_CONTENT_VALUE_OPTIONS = frozenset(
    {
        "-path",
        "-literalpath",
        "-totalcount",
        "-tail",
        "-encoding",
        "-readcount",
        "-delimiter",
        "-filter",
        "-include",
        "-exclude",
        "-credential",
        "-stream",
    }
)
_GET_CONTENT_SWITCHES = frozenset({"-raw", "-wait", "-force", "-asbytestream"})


def prints_file_body(name: str, args: tuple[str, ...]) -> bool:
    """読取コマンドが対象ファイルの本文を出力するかを返す。

    rg・grepの一覧・件数・真偽だけのモードと、sedのその場書換えは本文を出さない。
    """
    if name in {"rg", "grep"} and any(arg in _LISTING_ONLY_OPTIONS for arg in args):
        return False
    return not (name == "sed" and any(arg == "--in-place" or arg.startswith(("-i", "--in-place=")) for arg in args))


def get_content_targets(args: tuple[str, ...]) -> tuple[list[str], str | None]:
    """Get-Contentの読取対象と、対象境界を静的に確定できない場合の理由を返す。"""
    targets: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        option = arg.casefold()
        if option in _GET_CONTENT_VALUE_OPTIONS:
            if index + 1 >= len(args):
                return [], "Get-Contentのオプションと対象の境界を静的に確定できない"
            if option in ("-path", "-literalpath"):
                targets.append(args[index + 1])
            index += 2
            continue
        if arg.startswith("-") and option not in _GET_CONTENT_SWITCHES:
            return [], "Get-Contentのオプションと対象の境界を静的に確定できない"
        if not arg.startswith("-"):
            targets.append(arg)
        index += 1
    return targets, None


def processes_output(command: str) -> bool:
    """実行位置と接続した標準入力から、その場のコード実行と保存出力の加工を判定する。"""
    simple_commands = shell_simple_commands(command)
    masked = mask_substitutions(heredocs.mask_heredoc_bodies(command))
    source = masked[0] if masked is not None else command
    values = session_evidence_static.StaticValues(
        [simple.words for simple in simple_commands if simple.source == source], source
    )
    pipelines = [
        variants
        for pipeline in shell_segments.extract_execution_pipelines(command)
        for variants in itertools.product(
            *(
                [
                    shell_segments.resolve_execution_segment(list(words))
                    for words in values.variants(segment.raw_tokens or segment.tokens)
                ]
                for segment in pipeline
            )
        )
    ]
    for pipeline in pipelines:
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
    """処理内で構文を再利用し、呼び出しごとに独立したリストを返す。"""
    cache = _SHELL_PARSE_CACHE.get()
    if cache is None:
        return _parse_shell_simple_commands(command)
    if command in cache:
        cache.move_to_end(command)
        return list(cache[command])
    result = tuple(_parse_shell_simple_commands(command))
    cache[command] = result
    if len(cache) > 128:
        cache.popitem(last=False)
    return list(result)


def _parse_shell_simple_commands(command: str) -> list[SimpleCommand]:
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
