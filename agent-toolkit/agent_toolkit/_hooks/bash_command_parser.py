"""Bashコマンドの区間と実行位置を抽出するヘルパー。

`;`・`&&`・`||`・`|`・`&`で区切られたセグメントを分割し、実行前置語を解決した実行位置のトークン列を返す。
`cd`・`pushd`による現在ディレクトリの変化も解決する。
シェル展開を含むcwdは静的に解決できないため、解決不能として明示する。
"""

from __future__ import annotations

import dataclasses
import enum
import os
import os.path
import re
import shlex
from collections.abc import Sequence

_ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_]\w*=")
_PYTHON_TOKEN_PATTERN = re.compile(r"^python[0-9.]*(?:\.exe)?$", re.IGNORECASE)

_GLOBAL_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "-C",
        "-c",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "--config-env",
        "--list-cmds",
    }
)

_GLOBAL_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--no-pager",
        "-p",
        "--paginate",
        "--bare",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--no-optional-locks",
        "--exec-path",
        "--html-path",
        "--man-path",
        "--info-path",
        "--help",
        "--version",
    }
)

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

_UV_TERMINAL_OPTIONS: frozenset[str] = frozenset({"--help", "-h", "--version", "-V"})
"""後続の指定を実行しない終端オプション。走査中のコマンド自身を実行位置として確定する。"""


_UV_GLOBAL_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--allow-insecure-host",
        "--cache-dir",
        "--color",
        "--config-file",
        "--directory",
        "--project",
    }
)
"""`uv --help`（uv 0.12.3）の出力から機械抽出した、値を1つ取るグローバルオプション。

長形と短縮形の双方を保持する。`sudo`・`env`・`xargs`・`timeout`が表を持たず`-`始まりトークンで
一律に実行位置未確定へ倒すのに対し、`uv`だけがオプション表を持つのは、導入版の`--help`出力から
オプション全体を一次資料として取得できるためである。
表にない`-`始まりトークンは意味を確定できないため、その区間を実行位置未確定として扱う。
uvの新版でオプションが増減した場合は、`uv --help`と`uv run --help`の出力から本表と関連3表を再作成する。
"""

_UV_GLOBAL_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--managed-python",
        "--no-cache",
        "--no-config",
        "--no-managed-python",
        "--no-progress",
        "--no-python-downloads",
        "--offline",
        "--quiet",
        "--system-certs",
        "--verbose",
        "-n",
        "-q",
        "-v",
    }
)
"""`uv --help`の出力から機械抽出した、値を取らないグローバルオプション。取得元は`_UV_GLOBAL_OPTIONS_WITH_VALUE`参照。"""

_UV_RUN_OPTIONS_WITH_VALUE: frozenset[str] = frozenset(
    {
        "--allow-insecure-host",
        "--cache-dir",
        "--color",
        "--config-file",
        "--config-setting",
        "--config-settings-package",
        "--default-index",
        "--directory",
        "--env-file",
        "--exclude-newer",
        "--exclude-newer-package",
        "--extra",
        "--extra-index-url",
        "--find-links",
        "--fork-strategy",
        "--group",
        "--index",
        "--index-strategy",
        "--index-url",
        "--keyring-provider",
        "--link-mode",
        "--no-binary-package",
        "--no-build-isolation-package",
        "--no-build-package",
        "--no-editable-package",
        "--no-extra",
        "--no-group",
        "--no-sources-package",
        "--only-group",
        "--package",
        "--prerelease",
        "--prerelease-package",
        "--project",
        "--python",
        "--python-platform",
        "--refresh-package",
        "--reinstall-package",
        "--resolution",
        "--upgrade-group",
        "--upgrade-package",
        "--with",
        "--with-editable",
        "--with-requirements",
        "-C",
        "-P",
        "-f",
        "-i",
        "-p",
        "-w",
    }
)
"""`uv run --help`の出力から機械抽出した、値を1つ取る`run`オプション。取得元は`_UV_GLOBAL_OPTIONS_WITH_VALUE`参照。"""

_UV_RUN_OPTIONS_WITHOUT_VALUE: frozenset[str] = frozenset(
    {
        "--active",
        "--all-extras",
        "--all-groups",
        "--all-packages",
        "--compile-bytecode",
        "--exact",
        "--frozen",
        "--gui-script",
        "--isolated",
        "--locked",
        "--managed-python",
        "--module",
        "--no-binary",
        "--no-build",
        "--no-build-isolation",
        "--no-cache",
        "--no-config",
        "--no-default-groups",
        "--no-dev",
        "--no-editable",
        "--no-env-file",
        "--no-index",
        "--no-managed-python",
        "--no-progress",
        "--no-project",
        "--no-python-downloads",
        "--no-sources",
        "--no-sync",
        "--offline",
        "--only-dev",
        "--quiet",
        "--refresh",
        "--reinstall",
        "--script",
        "--system-certs",
        "--upgrade",
        "--verbose",
        "-U",
        "-m",
        "-n",
        "-q",
        "-s",
        "-v",
    }
)
"""`uv run --help`の出力から機械抽出した、値を取らない`run`オプション。取得元は`_UV_GLOBAL_OPTIONS_WITH_VALUE`参照。"""


_PIPE_SEPARATORS: frozenset[str] = frozenset({"|", "|&"})
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
    if separator in _PIPE_SEPARATORS:
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
        shell_argument = _shell_c_argument(segment.tokens) if segment.resolved else None
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


def _shell_c_argument(tokens: Sequence[str]) -> str | None:
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
    index = _skip_env_assignments(tokens, 0)
    is_agent_toolkit_script = False
    while index < len(tokens):
        token = tokens[index]
        if token in _EXEC_PREFIX_WITH_ENV_ASSIGNMENTS:
            index = _skip_env_assignments(tokens, index + 1)
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
            uv_index = _resolve_uv_execution_index(tokens, index)
            if uv_index is None:
                return ExecutionSegment((), False)
            if uv_index == index:
                break
            is_agent_toolkit_script = _is_agent_toolkit_script_invocation(tokens, index, uv_index)
            index = uv_index
            continue
        if is_python_token(token) and index + 1 < len(tokens) and tokens[index + 1] == "-m":
            index += 2
            continue
        break
    if index >= len(tokens) or tokens[index].startswith("-"):
        return ExecutionSegment((), False)
    return ExecutionSegment(tuple(tokens[index:]), True, is_agent_toolkit_script)


class _BashOutput(enum.Enum):
    PIPE = "pipe"
    UNKNOWN = "unknown"


type _OutputTarget = str | int | _BashOutput


@dataclasses.dataclass(frozen=True)
class BashInvocation:
    """静的に得た外側の引数と、その実行へ作用する出力接続を保持する。

    展開を含む引数は`arguments_known`を偽にし、値を実行して求めない。
    出力の整数は囲むシェルから継承するファイル記述子を表す。
    助言と実行記録が共有する`ExecutionSegment`の既存解析は変更しない。
    """

    segment: ExecutionSegment
    arguments_known: bool
    outputs: tuple[_OutputTarget, _OutputTarget] = (1, 2)
    background: bool = False
    captured: bool = False

    @property
    def output_pipe(self) -> bool:
        """標準出力または標準エラーが後段のパイプへ渡るかを返す。"""
        return _BashOutput.PIPE in self.outputs

    @property
    def output_redirected(self) -> bool:
        """標準出力か標準エラーの最終的な接続先が、呼び出し元から引き継いだ1と2からリダイレクトで変わったかを返す。

        接続先がファイル（`/dev/null`を含む）、別のファイル記述子、未確定のパスのいずれでも真とする。
        """
        return self.outputs != (1, 2)


@dataclasses.dataclass(frozen=True)
class _BashToken:
    value: str
    operator: bool = False
    known: bool = True
    nested: tuple[str, ...] = ()
    assignment: bool = False


_BASH_REDIRECTION = re.compile(r"[0-9]*(?:&>>|&>|<<-|<<<|>>|<<|<>|<&|>&|>\||>|<)")
_BASH_OPERATORS = ("&&", "||", "|&", ";;", ";", "&", "|", "(", ")", "{", "}", "\n")
_UNKNOWN_BASH_WORD = "\x00"


def extract_bash_invocations(command: str) -> list[BashInvocation]:
    """引用・置換・出力接続を保持し、判定できるBashの実行位置を返す。

    heredoc本文とリテラル引数を実行位置にせず、置換の内側は別の呼び出しとして解析する。
    区切り語を引用しないheredocの本文にある置換は本関数では解析せず、`heredoc_command_substitutions`が返す。
    PreToolUseはその置換を含むコマンドを実行前に遮断するため、本文の置換の内側を個別の判定へ渡さない。
    変数や別ファイルに隠れた起動、未対応の複合構文、閉じない構文から実行位置を推定しない。
    """
    try:
        tokens = _bash_tokens(mask_heredoc_bodies(command))
        invocations, end = _bash_command_list(tokens, 0)
    except ValueError:
        return []
    return invocations if end == len(tokens) else []


def _bash_tokens(command: str) -> list[_BashToken]:
    """演算子とシェル語を区別し、置換内の語を外側のトークンへ混ぜない。"""
    tokens: list[_BashToken] = []
    index = 0
    while index < len(command):
        if command[index] in " \t\r":
            index += 1
            continue
        if command.startswith("\\\n", index):
            index += 2
            continue
        if command[index] == "#":
            end = command.find("\n", index)
            index = len(command) if end < 0 else end
            continue
        if command.startswith("((", index):
            _, index = _bash_substitution(command, index)
            tokens.append(_BashToken(_UNKNOWN_BASH_WORD, known=False))
            continue
        # プロセス置換はリダイレクト演算子とは別のシェル語である。
        redirect = None if command[index : index + 2] in {"<(", ">("} else _BASH_REDIRECTION.match(command, index)
        operator = next((value for value in _BASH_OPERATORS if command.startswith(value, index)), None)
        if redirect is not None or operator is not None:
            value = redirect.group() if redirect is not None else operator
            assert value is not None
            tokens.append(_BashToken(value, operator=True))
            index += len(value)
            continue
        word, index = _bash_word(command, index)
        tokens.append(word)
    return tokens


def _bash_word(command: str, start: int) -> tuple[_BashToken, int]:
    """1つの語の引用と展開を区別し、静的な値と置換の本文を返す。"""
    pieces: list[str] = []
    nested: list[str] = []
    quote: str | None = None
    known = True
    index = start
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            if index + 1 >= len(command):
                raise ValueError("閉じないエスケープ")
            if command[index + 1] != "\n":
                pieces.append(command[index : index + 2])
            index += 2
            continue
        if char == quote:
            quote = None
            pieces.append(char)
            index += 1
            continue
        if quote != "'" and (
            command.startswith(("$(", "${"), index)
            or char == "`"
            or (quote is None and command[index : index + 2] in {"<(", ">("})
        ):
            body, index = _bash_substitution(command, index)
            if body is not None:
                nested.append(body)
            pieces.append(_UNKNOWN_BASH_WORD)
            known = False
            continue
        if quote is None and char in {"'", '"'}:
            quote = char
        elif quote is None and (char.isspace() or char in ";&|()<>"):
            break
        elif (
            quote != "'"
            and char == "$"
            and index + 1 < len(command)
            and (command[index + 1].isalnum() or command[index + 1] in "{_@*#?!-$")
            or quote is None
            and (char in "*?[" or (char == "~" and index == start))
        ):
            known = False
        pieces.append(char)
        index += 1
    if quote is not None or not pieces:
        raise ValueError("閉じない引用または未対応の語")
    values = shlex.split("".join(pieces), posix=True)
    if len(values) != 1:
        raise ValueError("シェル語を確定できない")
    value = values[0]
    if not known:
        assignment = _ENV_ASSIGN_PATTERN.match(value)
        value = (assignment.group() if assignment else "") + _UNKNOWN_BASH_WORD
    return _BashToken(
        value, known=known, nested=tuple(nested), assignment=_ENV_ASSIGN_PATTERN.match("".join(pieces)) is not None
    ), index


def _bash_substitution(command: str, start: int) -> tuple[str | None, int]:
    """置換の対応する終端を、内側の引用と入れ子を区別して求める。"""
    backtick = command[start] == "`"
    parameter = command.startswith("${", start)
    arithmetic = command.startswith("((", start)
    opening, closing = ("{", "}") if parameter else ("(", ")")
    cursor = start + (1 if backtick else 2)
    body_start = cursor
    depth = 2 if arithmetic else 1
    quote: str | None = None
    while cursor < len(command):
        char = command[cursor]
        if char == "\\" and quote != "'":
            cursor += 2
            continue
        if backtick and char == "`":
            return command[body_start:cursor], cursor + 1
        if char == quote:
            quote = None
        elif quote is None and char in {"'", '"'}:
            quote = char
        elif quote != "'" and (command.startswith(("$(", "${"), cursor) or (char == "`" and not backtick)):
            _, cursor = _bash_substitution(command, cursor)
            continue
        elif quote is None and char == opening:
            depth += 1
        elif quote is None and char == closing:
            depth -= 1
            if depth == 0:
                body = command[body_start:cursor]
                return (None if parameter or arithmetic or body.startswith("(") else body), cursor + 1
        cursor += 1
    raise ValueError("閉じない置換")


def _bash_command_list(
    tokens: Sequence[_BashToken], start: int, closing: str | None = None
) -> tuple[list[BashInvocation], int]:
    """単純なコマンドと括弧・波括弧のグループへ接続を対応付ける。"""
    result: list[BashInvocation] = []
    pipeline_start = 0
    index = start
    while index < len(tokens):
        if tokens[index].operator and tokens[index].value == closing:
            return result, index + 1
        if tokens[index].operator and tokens[index].value in {";", "\n", "&&", "||"}:
            pipeline_start = len(result)
            index += 1
            continue
        grouped = tokens[index].operator and tokens[index].value in {"(", "{"}
        if grouped:
            inner_closing = ")" if tokens[index].value == "(" else "}"
            current, index = _bash_command_list(tokens, index + 1, inner_closing)
            words: list[_BashToken] = []
        else:
            current = []
            words = []
        redirects: list[tuple[str, _BashToken]] = []
        while index < len(tokens):
            token = tokens[index]
            if token.operator:
                if _BASH_REDIRECTION.fullmatch(token.value):
                    if index + 1 >= len(tokens) or tokens[index + 1].operator:
                        raise ValueError("リダイレクト先がない")
                    redirects.append((token.value, tokens[index + 1]))
                    index += 2
                    continue
                break
            if grouped:
                raise ValueError("グループの後ろの未対応の語")
            words.append(token)
            index += 1
        separator = tokens[index].value if index < len(tokens) else ""
        if separator in {"(", "{", ")", "}"} and separator != closing:
            raise ValueError("未対応のグループ境界")
        if not grouped:
            current = _bash_word_invocations(words)
        outputs = _bash_output_targets(redirects, separator)
        current = [_inherit_bash_outputs(item, outputs) for item in current]
        result.extend(current)
        if separator == "&":
            result[pipeline_start:] = [dataclasses.replace(item, background=True) for item in result[pipeline_start:]]
        if separator in {";", "\n", "&&", "||", "&"}:
            pipeline_start = len(result)
        if separator == closing:
            return result, index + 1
        if not separator:
            break
        index += 1
    if closing is not None:
        raise ValueError("閉じないグループ")
    return result, index


def _bash_word_invocations(words: Sequence[_BashToken]) -> list[BashInvocation]:
    """実行前置語を解決し、引用されたシェル本文と能動的な置換を解析する。"""
    result: list[BashInvocation] = []
    values = [word.value for word in words]
    prefix = 0
    while prefix < len(words) and words[prefix].assignment:
        prefix += 1
    # 引用された名前や`=`はシェルの前置代入にならず、その語自身が実行位置になる。
    quoted_assignment = prefix < len(words) and _ENV_ASSIGN_PATTERN.match(values[prefix]) is not None
    segment = ExecutionSegment(tuple(values[prefix:]), True) if quoted_assignment else resolve_execution_segment(values)
    known = not any(_UNKNOWN_BASH_WORD in token for token in segment.tokens)
    if segment.resolved and segment.tokens and _UNKNOWN_BASH_WORD not in segment.tokens[0]:
        shell = _shell_c_argument(segment.tokens)
        if shell is not None and known:
            result.extend(extract_bash_invocations(shell))
        else:
            result.append(BashInvocation(segment, known))
    for word in words:
        for body in word.nested:
            result.extend(dataclasses.replace(item, captured=True) for item in extract_bash_invocations(body))
    return result


def _bash_output_targets(redirects: Sequence[tuple[str, _BashToken]], separator: str) -> tuple[_OutputTarget, _OutputTarget]:
    """リダイレクトを左から適用し、後段へ実際に渡る出力を求める。"""
    outputs: dict[int, _OutputTarget] = {1: _BashOutput.PIPE if separator in _PIPE_SEPARATORS else 1, 2: 2}
    for raw, operand in redirects:
        operator = raw.lstrip("0123456789")
        fd = int(raw[: len(raw) - len(operator)] or ("0" if operator.startswith("<") else "1"))
        if operator.startswith("<") or fd not in {1, 2}:
            continue
        if not operand.known:
            target: _OutputTarget = _BashOutput.UNKNOWN
        elif operator == ">&":
            target = outputs.get(int(operand.value), _BashOutput.UNKNOWN) if operand.value.isdecimal() else _BashOutput.UNKNOWN
        else:
            target = operand.value
        outputs[fd] = target
        if operator.startswith("&>"):
            outputs[2] = target
    if separator == "|&":
        outputs[2] = outputs[1]
    return outputs[1], outputs[2]


def _inherit_bash_outputs(invocation: BashInvocation, outputs: tuple[_OutputTarget, _OutputTarget]) -> BashInvocation:
    """グループの出力先を、内側で上書きされていない接続へ渡す。"""
    inherited = tuple(
        outputs[value - 1] if isinstance(value, int) and not (invocation.captured and value == 1) else value
        for value in invocation.outputs
    )
    return dataclasses.replace(invocation, outputs=(inherited[0], inherited[1]))


def is_python_token(token: str) -> bool:
    """`python`・`python3`・`python3.12`などの実行ファイル名なら真を返す。"""
    return _PYTHON_TOKEN_PATTERN.match(token) is not None


def has_uv_terminal_option(tokens: Sequence[str]) -> bool:
    """トークン列に`uv`の終端オプションが含まれる場合に真を返す。"""
    return any(token in _UV_TERMINAL_OPTIONS for token in tokens)


def _resolve_uv_execution_index(tokens: list[str], uv_index: int) -> int | None:
    """`uv`トークンの位置から実行位置の添字を求める。実行位置未確定の場合はNoneを返す。

    `uv`のグローバル区間と`run`区間へ同じ優先順位の走査（`_scan_uv_options`）を適用する。
    終端オプションを含む区間と`run`以外のサブコマンドは、`uv`自身を実行位置として確定する
    （検証コマンド・`codex exec`のいずれとも一致しないため検出対象にならない）。
    """
    index, state = _scan_uv_options(tokens, uv_index + 1, _UV_GLOBAL_OPTIONS_WITH_VALUE, _UV_GLOBAL_OPTIONS_WITHOUT_VALUE)
    if state == "terminal":
        return uv_index
    if state != "reached":
        return None
    if tokens[index] != "run":
        return uv_index
    index, state = _scan_uv_options(tokens, index + 1, _UV_RUN_OPTIONS_WITH_VALUE, _UV_RUN_OPTIONS_WITHOUT_VALUE)
    if state == "terminal":
        return uv_index
    if state != "reached":
        return None
    return index


def _is_agent_toolkit_script_invocation(tokens: Sequence[str], uv_index: int, execution_index: int) -> bool:
    """pluginプロジェクト配下のスクリプトの起動と、独立したリモート補助スクリプトの起動を識別する。"""
    index, state = _scan_uv_options(list(tokens), uv_index + 1, _UV_GLOBAL_OPTIONS_WITH_VALUE, _UV_GLOBAL_OPTIONS_WITHOUT_VALUE)
    if state != "reached" or index >= len(tokens) or tokens[index] != "run":
        return False
    run_index = index
    index, state = _scan_uv_options(list(tokens), run_index + 1, _UV_RUN_OPTIONS_WITH_VALUE, _UV_RUN_OPTIONS_WITHOUT_VALUE)
    if state != "reached" or index != execution_index:
        return False
    script_path = tokens[execution_index]
    normalized = script_path.replace("\\", "/")
    run_options = tokens[run_index + 1 : execution_index]
    if any(option in run_options for option in ("--script", "-s")):
        return normalized.endswith(
            (
                "/agent-toolkit/scripts/atk_serve_plans_remote_helper.py",
                "/agent-toolkit/scripts/atk_serve_sessions_remote_helper.py",
            )
        )
    project = _uv_project_option(run_options)
    if project is None or not normalized.endswith(".py"):
        return False
    normalized_project = project.replace("\\", "/").rstrip("/")
    if not normalized.startswith(f"{normalized_project}/"):
        return False
    relative = normalized.removeprefix(f"{normalized_project}/")
    return relative.startswith("agent_toolkit/") or (relative.startswith("skills/") and "/scripts/" in relative)


def _uv_project_option(options: Sequence[str]) -> str | None:
    """`uv run`のオプション列から明示されたproject rootを返す。"""
    for index, option in enumerate(options):
        if option in ("--project", "-p") and index + 1 < len(options):
            return options[index + 1]
        if option.startswith("--project="):
            return option.partition("=")[2]
    return None


def _scan_uv_options(
    tokens: list[str],
    start: int,
    with_value: frozenset[str],
    without_value: frozenset[str],
) -> tuple[int, str]:
    """`uv`のオプション列を走査し、到達位置と走査結果の状態を返す。

    解析の前提は「意味を確定できる構文だけを受理する」ことであり、個別のオプション名を事象ごとに追加しない。
    各トークンは次の5状態のいずれか1つへ排他的に定まる。判定はこの優先順位で行い、
    先に一致した状態で確定して以降の状態を評価しない。

    1. 終端状態: 終端オプション。この区間は後続の指定を実行しないため走査を終える（状態`terminal`）
    2. 値あり状態: 値ありオプション表と完全一致する。トークンと続く1トークンを走査対象から除く。
       `--name=value`形式は`--name`が同表と完全一致する場合に1トークンだけを除く
    3. 値なし状態: 値なしオプション表と完全一致する。トークン1つを除く
    4. 非オプション状態: `-`で始まらない。そのトークンを走査の到達点とする（状態`reached`）
    5. 未分類状態: 上記のいずれにも当たらない（表に無い長形、2文字以上の結合短縮形、表に無い短縮形など）。
       区間全体を実行位置未確定とする（状態`unresolved`）

    5状態は排他かつ網羅であり、優先順位が固定されているため同じトークンが2つの状態へ当たることはない。
    値なしオプション表に`--help`・`-h`が含まれていても、終端状態を最優先で判定するため状態1で確定する。
    新しいオプションや未知の記法が現れても個別の規則追加を要さず状態5へ倒れ、助言するかの判定では検出しない。
    """
    index = start
    while index < len(tokens):
        token = tokens[index]
        if has_uv_terminal_option((token,)):
            return index, "terminal"
        if token in with_value:
            index += 2
            continue
        name, separator, _ = token.partition("=")
        if separator and name in with_value:
            index += 1
            continue
        if token in without_value:
            index += 1
            continue
        if not token.startswith("-"):
            return index, "reached"
        return index, "unresolved"
    return index, "unresolved"


_PUSHD_STACK_ROTATION_PATTERN = re.compile(r"^[+-]\d+$")


@dataclasses.dataclass(frozen=True)
class CwdResolution:
    """シェルコマンドから得たcwdの解決結果を表す。"""

    path: str
    resolved: bool
    unresolved_expression: str | None = None


@dataclasses.dataclass
class QuotingScanner:
    """引用とエスケープの状態を保ちながらシェル文字列を1文字ずつ走査する。

    `consume_quoted`が真を返した位置は、エスケープ指定・エスケープされた文字・
    引用の内側のいずれかに属し、その位置まで`index`が進む。偽を返した位置は
    引用の外側にあり、呼び出し側が固有の解釈を加えて`index`を進める。
    引用の開始は呼び出し側が判定し、`enter_quote`で状態へ反映する。
    走査を終えた時点で`quote`が`None`でない場合、入力の引用は閉じていない。
    """

    text: str
    index: int = 0
    quote: str | None = None
    escaped: bool = False

    def consume_quoted(self) -> bool:
        """現在位置がエスケープまたは引用に属する場合、位置を進めて真を返す。"""
        char = self.text[self.index]
        if self.escaped:
            self.escaped = False
            self.index += 1
            return True
        if char == "\\" and self.quote != "'":
            self.escaped = True
            self.index += 1
            return True
        if self.quote is not None:
            if char == self.quote:
                self.quote = None
            self.index += 1
            return True
        return False

    def enter_quote(self, quote: str) -> None:
        """引用の開始位置で呼び、引用状態へ入って位置を進める。"""
        self.quote = quote
        self.index += 1


@dataclasses.dataclass(frozen=True)
class HeredocDeclaration:
    r"""コマンド行にある1つのheredocの宣言。

    `delimiter`はbashと同じく区切り語から引用を除いた語であり、終端行との比較に使う。
    `expands`は区切り語のどこにも引用（`'`・`"`・`\\`）が無く、bashが本文を展開することを表す。
    """

    delimiter: str
    strip_tabs: bool
    expands: bool


@dataclasses.dataclass(frozen=True)
class HeredocBody:
    """コマンド文字列上のheredoc本文と終端行の範囲。

    本文は`[start, end)`、終端行の内容（改行を除く）は`[end, terminator_end)`とする。
    終端行が無いまま末尾へ達した本文では`end`と`terminator_end`が文字列の長さに一致する。
    """

    start: int
    end: int
    terminator_end: int
    expands: bool


_HEREDOC_DELIMITER_END = frozenset(" \t\r\n;|&<>()")
_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


def _heredoc_delimiter(line: str, start: int) -> tuple[str, bool, int] | None:
    """区切り語を読み、引用を除いた語、引用の有無および語の直後の位置を返す。

    bashは区切り語へquote removalだけを適用し、語のどこかに引用があれば本文を展開しない。
    引用が閉じない場合はNoneを返す。
    """
    pieces: list[str] = []
    quoted = False
    index = start
    while index < len(line) and line[index] not in _HEREDOC_DELIMITER_END:
        char = line[index]
        if char == "\\":
            if index + 1 >= len(line):
                return None
            pieces.append(line[index + 1])
            quoted = True
            index += 2
        elif char == "'":
            end = line.find("'", index + 1)
            if end < 0:
                return None
            pieces.append(line[index + 1 : end])
            quoted = True
            index = end + 1
        elif char == '"':
            index += 1
            while index < len(line) and line[index] != '"':
                if line[index] == "\\" and index + 1 < len(line) and line[index + 1] in _DOUBLE_QUOTE_ESCAPABLE:
                    index += 1
                pieces.append(line[index])
                index += 1
            if index >= len(line):
                return None
            quoted = True
            index += 1
        else:
            pieces.append(char)
            index += 1
    return "".join(pieces), quoted, index


def _heredoc_declarations(line: str) -> list[HeredocDeclaration]:
    """コマンド行にあるheredocの宣言を出現順に返す。"""
    declarations: list[HeredocDeclaration] = []
    scanner = QuotingScanner(line)
    arithmetic_depth = 0
    word_boundary = True
    while scanner.index < len(line):
        was_escaped = scanner.escaped
        if scanner.consume_quoted():
            if was_escaped:
                word_boundary = False
            continue
        index = scanner.index
        char = line[index]
        if arithmetic_depth:
            if char == "(":
                arithmetic_depth += 1
            elif char == ")":
                arithmetic_depth -= 1
            scanner.index += 1
            continue
        if char in {"'", '"'}:
            word_boundary = False
            scanner.enter_quote(char)
            continue
        if line.startswith("$((", index):
            arithmetic_depth = 2
            word_boundary = False
            scanner.index += 3
            continue
        if line.startswith("((", index):
            arithmetic_depth = 2
            word_boundary = False
            scanner.index += 2
            continue
        if char == "#" and word_boundary:
            break
        if not line.startswith("<<", index) or line.startswith("<<<", index):
            word_boundary = char in " \t;&|()"
            scanner.index += 1
            continue

        cursor = index + 2
        strip_tabs = cursor < len(line) and line[cursor] == "-"
        if strip_tabs:
            cursor += 1
        while cursor < len(line) and line[cursor] in {" ", "\t"}:
            cursor += 1
        parsed = _heredoc_delimiter(line, cursor)
        if parsed is None:
            break
        delimiter, quoted, cursor = parsed
        if delimiter:
            declarations.append(HeredocDeclaration(delimiter, strip_tabs, expands=not quoted))
        word_boundary = False
        scanner.index = cursor
    return declarations


def heredoc_bodies(command: str) -> list[HeredocBody]:
    """コマンド文字列にあるheredocの本文と終端行の範囲を出現順に返す。

    本文の範囲と展開の有無は`_heredoc_declarations`の1つの定義から得る。
    本文のマスクと、展開される本文の置換の判定はいずれも本関数の結果を使う。
    """
    bodies: list[HeredocBody] = []
    line_start = 0
    while line_start < len(command):
        line_end = command.find("\n", line_start)
        if line_end < 0:
            line_end = len(command)
        declarations = _heredoc_declarations(command[line_start:line_end])
        cursor = line_end + (line_end < len(command))
        for declaration in declarations:
            body_start = cursor
            body_end = terminator_end = len(command)
            while cursor < len(command):
                body_line_end = command.find("\n", cursor)
                if body_line_end < 0:
                    body_line_end = len(command)
                content_end = body_line_end - (body_line_end > cursor and command[body_line_end - 1] == "\r")
                content = command[cursor:content_end]
                candidate = content.lstrip("\t") if declaration.strip_tabs else content
                line_head = cursor
                cursor = body_line_end + (body_line_end < len(command))
                if candidate == declaration.delimiter:
                    body_end, terminator_end = line_head, body_line_end
                    break
            bodies.append(HeredocBody(body_start, body_end, terminator_end, declaration.expands))
        line_start = cursor
    return bodies


def _blank_line(masked: list[str], start: int, end: int, *, separator: bool = False) -> None:
    """改行を保ち、指定範囲の行内容を空白または区切り標識へ置換する。"""
    for index in range(start, end):
        if masked[index] not in {"\r", "\n"}:
            masked[index] = " "
    if separator and start < end:
        masked[start] = ";"


def mask_heredoc_bodies(command: str) -> str:
    """heredoc本文を同じ長さの空白へ置換し、本文外の位置と改行数を保つ。

    終端行は区切り標識`;`へ置換し、heredoc以降のコマンドを別の区間として残す。
    """
    masked = list(command)
    for body in heredoc_bodies(command):
        _blank_line(masked, body.start, body.end)
        _blank_line(masked, body.end, body.terminator_end, separator=True)
    return "".join(masked)


def _active_substitutions(body: str) -> list[str]:
    r"""展開されるheredoc本文から、エスケープされていないバッククォートと`$(`の置換を返す。

    展開される本文では引用符は特別な意味を持たず、`\\`だけが次の1文字をリテラルにする。
    `$((`は算術展開として扱い、コマンド置換に数えない。閉じない置換はその行の末尾までを返す。
    """
    found: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 2
            continue
        if char == "`" or (body.startswith("$(", index) and not body.startswith("$((", index)):
            try:
                _, end = _bash_substitution(body, index)
            except ValueError:
                line_end = body.find("\n", index)
                end = len(body) if line_end < 0 else line_end
            found.append(body[index:end])
            index = end
            continue
        index += 1
    return found


def heredoc_command_substitutions(command: str) -> list[str]:
    """bashが本文を展開するheredocの本文にある、能動的なコマンド置換を出現順に返す。

    外側のコマンドに加え、`sh -c`・`bash -c`の引数とコマンド置換の本文の中にあるheredocも対象にする。
    引用付きの区切り語の本文と、`$VAR`・`${VAR}`・`$((...))`・エスケープ済みの表記は対象外とする。
    """
    found: list[str] = []
    for body in heredoc_bodies(command):
        if body.expands:
            found.extend(_active_substitutions(command[body.start : body.end]))
    try:
        tokens = _bash_tokens(mask_heredoc_bodies(command))
    except ValueError:
        tokens = []
    for nested in _nested_shell_texts(tokens):
        found.extend(heredoc_command_substitutions(nested))
    return list(dict.fromkeys(found))


def _nested_shell_texts(tokens: Sequence[_BashToken]) -> list[str]:
    """トークン列から、置換の本文と静的に確定した`sh -c`・`bash -c`の引数を返す。"""
    texts: list[str] = []
    words: list[_BashToken] = []
    for token in [*tokens, _BashToken(";", operator=True)]:
        if not token.operator:
            words.append(token)
            texts.extend(token.nested)
            continue
        segment = resolve_execution_segment([word.value for word in words])
        words = []
        if not segment.resolved or not segment.tokens:
            continue
        shell = _shell_c_argument(segment.tokens)
        if shell is not None and _UNKNOWN_BASH_WORD not in shell:
            texts.append(shell)
    return texts


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


def _skip_env_assignments(tokens: list[str], start: int) -> int:
    """先頭の`KEY=VALUE`形式の環境変数代入をスキップした次の位置を返す。"""
    i = start
    while i < len(tokens) and _ENV_ASSIGN_PATTERN.match(tokens[i]):
        i += 1
    return i


def resolve_cwd_change(tokens: list[str], current_cwd: CwdResolution) -> CwdResolution | None:
    """cwdを変更するセグメントの解決結果を返す。"""
    start = _skip_env_assignments(tokens, 0)
    if start >= len(tokens):
        return None
    if tokens[start] in ("cd", "pushd"):
        return _apply_cd(tokens, start, current_cwd)
    if tokens[start] == "popd":
        return CwdResolution("", False)
    return None


def _apply_cd(tokens: list[str], start: int, current_cwd: CwdResolution) -> CwdResolution:
    """`cd`・`pushd`の引数を解釈して新しいcwdの解決結果を返す。

    引数なし・オプション（`-`等）・シェル展開を含む場合は解決不能とする。
    相対パスは解決済みの現在cwdを基点に`os.path.normpath`で正規化する。
    """
    if start + 1 >= len(tokens):
        return CwdResolution("", False)
    arguments = tokens[start + 1 :]
    terminators = [index for index, argument in enumerate(arguments) if argument == "--"]
    first_terminator = terminators[0] if terminators else len(arguments)
    option_arguments = arguments[:first_terminator]
    if tokens[start] == "pushd":
        if "-n" in option_arguments:
            return current_cwd
        if option_arguments and _PUSHD_STACK_ROTATION_PATTERN.fullmatch(option_arguments[0]):
            return CwdResolution("", False)
    if len(terminators) > 1:
        return CwdResolution("", False)
    if terminators:
        target_index = terminators[0] + 1
        if target_index >= len(arguments):
            return CwdResolution("", False)
        target = arguments[target_index]
    else:
        target = arguments[0]
    if not target or target.startswith("-"):
        return CwdResolution("", False)
    return _normalize_relative(target, current_cwd)


# 引用符・エスケープで保護されたリテラルなメタ文字を解決済みとして救済する試みは、
# `--`終端・`pushd`オプション以外の全シェル字句規則の再実装を要する。
# バックスラッシュ・部分引用・`git -C`引数等で継続的に穴が生じた実績があり、費用対効果に見合わない。
# メタ文字を含む対象は常に解決不能とし、安全側（過剰block/warn）で運用する。
def _contains_shell_expansion(value: str) -> bool:
    """静的解析で解決できないシェル展開の記号を含むか判定する。"""
    return any(marker in value for marker in ("$", "`", "~", "*", "?", "[", "{"))


def _normalize_relative(target: str, current_cwd: CwdResolution) -> CwdResolution:
    """相対パスを現在cwd基点で正規化し、解決結果を返す。"""
    if _contains_shell_expansion(target):
        return CwdResolution("", False, target)
    if os.path.isabs(target):
        return CwdResolution(os.path.normpath(target), True)
    if not current_cwd.resolved:
        return CwdResolution("", False, current_cwd.unresolved_expression)
    return CwdResolution(os.path.normpath(os.path.join(current_cwd.path, target)), True)
