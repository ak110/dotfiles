"""Bashコマンドを区間とパイプラインへ分け、実行前置語を解決した実行位置のトークン列を返す処理。

`;`・`&&`・`||`・`|`・`&`で区切られた区間へ分割し、`sudo`・`env`・`timeout`・`uv run`などの実行前置語を除いた実行位置を求める。
実行位置を静的に確定できない区間は未確定として返す。
"""

from __future__ import annotations

import dataclasses
import re
import shlex
from collections.abc import Sequence

from agent_toolkit._common.heredocs import mask_heredoc_bodies
from agent_toolkit._common.uv_arguments import is_agent_toolkit_script_invocation, is_python_token, resolve_uv_execution_index

ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_]\w*=")


# --- Bash: 実行位置のトークン列抽出（助言の判定に共通して使う関数）---

# 本ヘルパーはPreToolUseで助言するかの判定とPostToolUseの実行済みコマンド記録が共有する。
# 遮断を伴う`_check_bash_process_kill_by_pattern`は、コマンド置換・サブシェル・改行・未知の前置語を
# 保持しない本解析を使わない。入力文字列上で安全な検索リテラルと確定できる場合だけ遮断を緩める
# （解析の不足で既存の保護を外さないため）。

_EXEC_PREFIX_WITH_ENV_ASSIGNMENTS: frozenset[str] = frozenset({"sudo", "env"})
"""続く`KEY=VALUE`形式の代入を走査対象から除く実行前置語。

`-`始まりトークンが続く場合は、引数を取るか否かが実装・版により異なり値の境界を確定できないため、
その区間を実行位置未確定として扱う。
"""

_EXEC_PREFIX_WITHOUT_OPTIONS: frozenset[str] = frozenset({"command", "nohup", "uvx", "xargs"})
"""次のトークンを実行位置候補とする実行前置語。

`-`始まりトークンが続く場合は`_EXEC_PREFIX_WITH_ENV_ASSIGNMENTS`と同じ理由で実行位置未確定とする。
"""

_TIMEOUT_DURATION_RE = re.compile(r"^\d+(?:\.\d+)?[smhd]?$")

_SHELL_TOKENS: frozenset[str] = frozenset({"sh", "bash"})


PIPE_SEPARATORS: frozenset[str] = frozenset({"|", "|&"})
"""前後の区間を同一パイプラインへ属させる区切り演算子。

`;`・`&&`・`||`・`&`は前段の標準出力を後段へ渡さないため、別のパイプラインの開始として扱う。
"""


@dataclasses.dataclass(frozen=True)
class ExecutionSegment:
    """Bashコマンドの1区間について、実行位置以降のトークン列と実行位置の確定可否を表す。

    `resolved`が偽の区間では`tokens`を空とし、助言するかを判定する処理は、その区間では検出しない。
    `is_agent_toolkit_script`はagent-toolkit配下から配布する自動チェックスクリプトを表す。
    `raw_tokens`は、実行位置が未確定の区間でリダイレクト先を解析するため、元のトークン列を保持する。
    `tokens`はリダイレクトの演算子と対象（`>`・`/tmp/x`・`2>&1`など）も含む。
    位置引数を数える消費側と、引数列全体を比べる消費側は、`agent_toolkit._common.shell_tokens`でリダイレクトを除いた引数列を使う。
    """

    tokens: tuple[str, ...]
    resolved: bool
    is_agent_toolkit_script: bool = False
    raw_tokens: tuple[str, ...] = ()


def _split_bash_pipelines(command: str) -> list[list[str]]:
    """`split_bash_segments`の分割結果を、パイプラインごとの区間列へまとめて返す。

    分割そのものは`split_bash_segments`が行い、本関数は境界の分類とまとめ直しだけを行う。
    各区間の元コマンド内の位置を先頭から順に求め、区間の間に残る文字列（空白と区切り演算子だけからなる）で
    同一パイプラインの継続かを判定する。位置を求められない場合は継続とみなさない。
    """
    pipelines: list[list[str]] = []
    segments = split_bash_segments(command)
    position = 0
    for index, segment in enumerate(segments):
        start = command.find(segment, position)
        separator_text = command[position:start] if start >= 0 else ""
        separator = separator_text.strip()
        previous = segments[index - 1] if index > 0 else ""
        if pipelines and _is_redirection_continuation(previous, separator_text, segment):
            pipelines[-1][-1] += separator_text + segment
            position = (start if start >= 0 else position) + len(segment)
            continue
        if index == 0 or not _is_pipeline_continuation(previous, separator, segment):
            pipelines.append([])
        pipelines[-1].append(segment)
        position = (start if start >= 0 else position) + len(segment)
    return pipelines


def _is_pipeline_continuation(previous: str, separator: str, following: str) -> bool:
    """区間の境界が同一パイプラインの継続であるかを、前後の区間と区切り文字列から判定する。"""
    if separator in PIPE_SEPARATORS:
        return True
    # `2>&1`・`&>log`等のリダイレクトに含まれる`&`は、`split_bash_segments`が区切りとして分割するが
    # コマンドの終端ではないため継続として扱う（前段の出力は後段のパイプへ渡る）。
    return separator == "&" and (previous.endswith((">", "<")) or following.startswith(">"))


def _is_redirection_continuation(previous: str, separator: str, following: str) -> bool:
    """`split_bash_segments`が分割したリダイレクト断片の続きであるかを返す。"""
    if separator.strip() != "&":
        return False
    if previous.endswith((">", "<")):
        return True
    return following.startswith(">") and separator.endswith("&")


def extract_execution_pipelines(command: str, *, expand_shell: bool = True) -> list[list[ExecutionSegment]]:
    """Bashコマンドをパイプライン単位へ分割し、各パイプラインの区間列を実行順で返す。

    1つのパイプラインは`|`だけで連結された一続きの区間列であり、前段の標準出力が後段へ渡る。
    `;`・`&&`・`||`・`&`は出力を渡さないため別のパイプラインとして分ける。
    前段の出力が後段へ渡るかを判定する処理は、同じパイプライン内の前後関係だけを見ればよい。

    区間分割は`split_bash_segments`（`;`・`&&`・`||`・`|`・`&`で分割し、クォート内のメタ文字を除く）、
    トークン化は`shlex.split(segment, posix=True)`を使う。
    実行前置語（`sudo`・`env`・`uv run`等）を解決した後の実行位置が`sh -c`・`bash -c`
    （`-lc`等の結合形を含む）である場合、続く文字列引数を1段だけ同じ手順で展開する。
    2段以上の入れ子は展開せず実行位置未確定とする。
    展開結果が1つのパイプラインへ収まる場合は、上流・下流とも呼び出し元のパイプラインへ連結する。
    内側が`;`・`&&`等により複数の文へ分かれる場合、上流と下流を非対称に扱う。

    - 下流（`sh -c '...' | 後続`）は連結する。内側の各文は同じ標準出力を継承し実行順に書き込むため、
      どの文の出力も後続へ渡る。内側の各文それぞれの末尾へ後続の区間列を複製して連結する
    - 上流（`前段 | sh -c '...'`）は連結しない。渡された標準入力をどの文が消費するかは
      実行時の消費順に依存し、静的なトークン列の解析では確定できないため、その区間を実行位置未確定とする

    本ヘルパーはコマンド置換・サブシェル・`--`によるオプション終端・前置語の値境界を解決しない。
    この解析水準で成立するのは、実行を止めない助言用の判定に限る。
    """
    pipelines: list[list[ExecutionSegment]] = []
    for raw_pipeline in _split_bash_pipelines(command):
        pipelines.extend(_resolve_pipeline(raw_pipeline, expand_shell=expand_shell))
    return [pipeline for pipeline in pipelines if pipeline]


def _resolve_pipeline(raw_segments: Sequence[str], *, expand_shell: bool) -> list[list[ExecutionSegment]]:
    """1つのパイプラインの区間列を解決する。

    戻り値の先頭は対象のパイプライン自身であり、2件目以降は`sh -c`展開により生じた独立したパイプラインとする。
    展開の接続規則は`extract_execution_pipelines`のdocstringが定める。
    """
    current: list[ExecutionSegment] = []
    for index, raw_segment in enumerate(raw_segments):
        try:
            tokens = shlex.split(raw_segment, posix=True)
        except ValueError:
            current.append(ExecutionSegment((), False))
            continue
        segment = dataclasses.replace(resolve_execution_segment(tokens), raw_tokens=tuple(tokens))
        shell_argument = shell_c_argument(segment.tokens) if segment.resolved else None
        if shell_argument is None:
            current.append(segment)
            continue
        if not expand_shell:
            current.append(ExecutionSegment((), False))
            continue
        inner = extract_execution_pipelines(shell_argument, expand_shell=False)
        if len(inner) <= 1:
            current.extend(inner[0] if inner else ())
            continue
        # 内側が複数の文へ分かれる場合、上流は接続せずその区間を実行位置未確定とする。
        # 下流の区間列は内側の各文へ複製して連結し、それぞれを独立したパイプラインとする。
        current.append(ExecutionSegment((), False))
        rest = _resolve_pipeline(raw_segments[index + 1 :], expand_shell=expand_shell)
        downstream = rest[0] if rest else []
        return [current, *(inner_pipeline + downstream for inner_pipeline in inner), *rest[1:]]
    return [current]


def extract_execution_segments(command: str) -> list[ExecutionSegment]:
    """Bashコマンドの全区間を実行順の一次元列で返す。

    パイプラインの区切りを条件に含めず、実行位置の一致だけを判定する処理が使う。
    """
    return [segment for pipeline in extract_execution_pipelines(command) for segment in pipeline]


def shell_c_argument(tokens: Sequence[str]) -> str | None:
    """実行位置以降のトークン列が`sh -c`・`bash -c`形式であれば、実行するコマンド文字列を返す。

    該当しない場合はNoneを返す。判定対象は実行前置語を解決した後のトークン列であり、
    `sudo sh -c '...'`のように前置語と組み合わせた形も展開対象となる。
    """
    if not tokens or tokens[0] not in _SHELL_TOKENS:
        return None
    for position in range(1, len(tokens)):
        token = tokens[position]
        if not token.startswith("-") or token.startswith("--"):
            return None
        if "c" in token[1:]:
            return tokens[position + 1] if position + 1 < len(tokens) else None
    return None


def resolve_execution_segment(tokens: list[str]) -> ExecutionSegment:
    """トークン列の実行位置を求め、実行位置以降のトークン列と確定可否を返す。

    先頭の`KEY=VALUE`形式の環境変数代入の次の位置から、既知の実行前置語を順に走査対象から除く。
    除いた後の位置が存在しない場合、またはそのトークンが`-`で始まる場合は、
    前置語の引数境界を確定できていないため実行位置未確定とする。
    """
    index = skip_env_assignments(tokens, 0)
    is_agent_toolkit_script = False
    while index < len(tokens):
        token = tokens[index]
        if token in _EXEC_PREFIX_WITH_ENV_ASSIGNMENTS:
            index = skip_env_assignments(tokens, index + 1)
            continue
        if token in _EXEC_PREFIX_WITHOUT_OPTIONS:
            index += 1
            continue
        if token == "timeout":
            index += 1
            if index < len(tokens) and _TIMEOUT_DURATION_RE.match(tokens[index]):
                index += 1
            continue
        if token == "uv":
            uv_index = resolve_uv_execution_index(tokens, index)
            if uv_index is None:
                return ExecutionSegment((), False)
            if uv_index == index:
                break
            is_agent_toolkit_script = is_agent_toolkit_script_invocation(tokens, index, uv_index)
            index = uv_index
            continue
        if is_python_token(token) and index + 1 < len(tokens) and tokens[index + 1] == "-m":
            index += 2
            continue
        break
    if index >= len(tokens) or tokens[index].startswith("-"):
        return ExecutionSegment((), False)
    return ExecutionSegment(tuple(tokens[index:]), True, is_agent_toolkit_script)


def nested_shell_positions(command: str) -> frozenset[int]:
    """置換構文、サブシェルおよびバッククォートの内側にある文字位置を返す。

    対応が閉じない構文は開始位置から末尾までを内側として扱う。補正位置を外側と
    誤認するより、補正を見送って元の入力をBashへ渡す方が入力を壊さないためである。
    heredoc本文は同じ長さの空白へ置換してから走査する。
    """
    masked = mask_heredoc_bodies(command)
    protected: set[int] = set()
    stack: list[int] = []
    quote: str | None = None
    backtick_start: int | None = None
    escaped = False
    index = 0
    while index < len(masked):
        char = masked[index]
        if escaped:
            if stack or backtick_start is not None:
                protected.add(index)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            if stack or backtick_start is not None:
                protected.add(index)
            escaped = True
            index += 1
            continue
        if quote is not None:
            if stack or backtick_start is not None:
                protected.add(index)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            if stack or backtick_start is not None:
                protected.add(index)
            quote = char
            index += 1
            continue
        if char == "`":
            protected.add(index)
            backtick_start = index if backtick_start is None else None
            index += 1
            continue
        if backtick_start is not None:
            protected.add(index)
            index += 1
            continue
        opens_nested = char == "(" and (
            bool(stack) or (index > 0 and masked[index - 1] in "$<>") or index == 0 or masked[index - 1].isspace()
        )
        if opens_nested:
            stack.append(index)
        if stack:
            protected.add(index)
            if char == ")":
                stack.pop()
        index += 1
    return frozenset(protected)


def split_bash_segments(command: str) -> list[str]:
    """Bashコマンドを`;`・`&&`・`||`・`|`・`&`・改行で分割する。

    クォート（`'`・`"`）内のメタ文字は分割対象外とする。
    heredoc本文は同じ長さの空白へ置換してから分割し、本文外の位置を保つ。
    行継続と置換構文、サブシェルおよびバッククォート内の演算子・改行も分割しない。
    `for`・`while`・`until`・`if`・`case`から対応する終端までの改行は、制御構造を
    外側の独立呼び出しへ分けないため保持する。内部の`;`では従来どおり判定する区間を分ける。
    """
    original = command
    command = mask_heredoc_bodies(command)
    nested = nested_shell_positions(original)
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    compound_depth = 0
    word: list[str] = []
    word_at_command_start = False
    command_start = True

    def finish_word() -> None:
        nonlocal command_start, compound_depth
        token = "".join(word)
        word.clear()
        if not token:
            return
        if word_at_command_start and token in {"for", "while", "until", "if", "case"}:
            compound_depth += 1
        elif word_at_command_start and token in {"done", "fi", "esac"} and compound_depth:
            compound_depth -= 1
        command_start = word_at_command_start and token in {"do", "then", "else", "elif"}

    i = 0
    while i < len(command):
        c = command[i]
        if i in nested:
            buf.append(original[i])
            i += 1
            continue
        if in_single:
            buf.append(c)
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(c)
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            buf.append(c)
            i += 1
            continue
        if c == '"':
            in_double = True
            buf.append(c)
            i += 1
            continue
        if c == "\\" and i + 1 < len(command) and command[i + 1] == "\n":
            buf.extend((c, "\n"))
            i += 2
            continue
        if c.isalnum() or c == "_":
            if not word:
                word_at_command_start = command_start
            word.append(c)
        else:
            finish_word()
        if c in ("&", "|") and i + 1 < len(command) and command[i + 1] == c:
            segments.append("".join(buf))
            buf = []
            command_start = True
            i += 2
            continue
        if c in (";", "&", "|"):
            segments.append("".join(buf))
            buf = []
            command_start = True
            i += 1
            continue
        if c == "\n" and compound_depth == 0:
            segments.append("".join(buf))
            buf = []
            command_start = True
            i += 1
            continue
        if c == "\n":
            command_start = True
        buf.append(c)
        i += 1
    if buf:
        segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def skip_env_assignments(tokens: list[str], start: int) -> int:
    """先頭の`KEY=VALUE`形式の環境変数代入をスキップした次の位置を返す。"""
    i = start
    while i < len(tokens) and ENV_ASSIGN_PATTERN.match(tokens[i]):
        i += 1
    return i
