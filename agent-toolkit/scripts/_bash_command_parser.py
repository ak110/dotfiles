"""Bashコマンドの実行位置とgit呼び出しイベントを抽出するヘルパー。

`;`・`&&`・`||`・`|`・`&`で区切られたセグメントを順に評価し、`cd`・`pushd`で
現在ディレクトリを追跡しながら、各git呼び出しごとに`GitEvent`を返す。
`git -C <dir>`の相対パスは出現時点の現在cwdを基点に正規化する。

シェル展開を含むcwdは静的に解決できないため、解決不能として明示する。

`pretooluse` / `posttooluse`の両方が同一の実行位置とイベント列を消費する形に統一している。
"""

from __future__ import annotations

import dataclasses
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

# --- Bash: 実行位置のトークン列抽出（助言用検査の共通入口）---

# 本ヘルパーはPreToolUseの助言用検査とPostToolUseの実行済みコマンド記録が共有する。
# 遮断を伴う`_check_bash_process_kill_by_pattern`は、コマンド置換・サブシェル・オプション終端まで
# 解決できる解析を用意できるまで現行のコマンド文字列全体への一致判定を維持し、本ヘルパーを使わない
# （解析の不足で既存の保護を外さないため）。

_EXEC_PREFIX_WITH_ENV_ASSIGNMENTS: frozenset[str] = frozenset({"sudo", "env"})
"""続く`KEY=VALUE`形式の代入を走査対象から除く実行前置語。

`-`始まりトークンが続く場合は、引数を取るか否かが実装・版により異なり値の境界を確定できないため、
当該区間を実行位置未確定として扱う。
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
表に無い`-`始まりトークンは意味を確定できないため当該区間を実行位置未確定として扱う。
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

    `resolved`が偽の区間では`tokens`を空とし、助言用検査は当該区間で検出しない。
    `is_agent_toolkit_script`はagent-toolkit配下の配布検査スクリプトを表す。
    """

    tokens: tuple[str, ...]
    resolved: bool
    is_agent_toolkit_script: bool = False


def _split_bash_pipelines(command: str) -> list[list[str]]:
    """`split_bash_segments`の分割結果を、パイプラインごとの区間列へまとめて返す。

    分割そのものは`split_bash_segments`を正本とし、本関数は境界の分類とまとめ直しだけを行う。
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
    前段の出力が後段へ渡るか否かを要件とする検査は、同じパイプライン内の前後関係だけを見ればよい。

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
      実行時の消費順に依存し、静的なトークン列の解析では確定できないため、当該区間を実行位置未確定とする

    本ヘルパーはコマンド置換・サブシェル・`--`によるオプション終端・前置語の値境界を解決しない。
    この解析水準で成立するのは、実行を止めない助言用の判定に限る。
    """
    pipelines: list[list[ExecutionSegment]] = []
    for raw_pipeline in _split_bash_pipelines(command):
        pipelines.extend(_resolve_pipeline(raw_pipeline, expand_shell=expand_shell))
    return [pipeline for pipeline in pipelines if pipeline]


def _resolve_pipeline(raw_segments: Sequence[str], *, expand_shell: bool) -> list[list[ExecutionSegment]]:
    """1つのパイプラインの区間列を解決する。

    戻り値の先頭は当該パイプライン自身であり、2件目以降は`sh -c`展開により生じた独立したパイプラインとする。
    展開の接続規則は`extract_execution_pipelines`のdocstringが定める。
    """
    current: list[ExecutionSegment] = []
    for index, raw_segment in enumerate(raw_segments):
        try:
            tokens = shlex.split(raw_segment, posix=True)
        except ValueError:
            current.append(ExecutionSegment((), False))
            continue
        segment = resolve_execution_segment(tokens)
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
        # 内側が複数の文へ分かれる場合、上流は接続せず当該区間を実行位置未確定とする。
        # 下流の区間列は内側の各文へ複製して連結し、それぞれを独立したパイプラインとする。
        current.append(ExecutionSegment((), False))
        rest = _resolve_pipeline(raw_segments[index + 1 :], expand_shell=expand_shell)
        downstream = rest[0] if rest else []
        return [current, *(inner_pipeline + downstream for inner_pipeline in inner), *rest[1:]]
    return [current]


def extract_execution_segments(command: str) -> list[ExecutionSegment]:
    """Bashコマンドの全区間を実行順の一次元列で返す。

    パイプラインの区切りを要件としない検査（実行位置の一致だけを判定する検査）が使う。
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
    除いた後の位置が存在しない場合、または当該トークンが`-`で始まる場合は、
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
    """`uv run --script`のagent-toolkit配下Pythonスクリプトを識別する。"""
    index, state = _scan_uv_options(list(tokens), uv_index + 1, _UV_GLOBAL_OPTIONS_WITH_VALUE, _UV_GLOBAL_OPTIONS_WITHOUT_VALUE)
    if state != "reached" or index >= len(tokens) or tokens[index] != "run":
        return False
    run_index = index
    index, state = _scan_uv_options(list(tokens), run_index + 1, _UV_RUN_OPTIONS_WITH_VALUE, _UV_RUN_OPTIONS_WITHOUT_VALUE)
    if state != "reached" or index != execution_index:
        return False
    script_path = tokens[execution_index]
    normalized = script_path.replace("\\", "/")
    components = tuple(part for part in normalized.split("/") if part)
    run_options = tokens[run_index + 1 : execution_index]
    return (
        any(option in run_options for option in ("--script", "-s"))
        and "agent-toolkit" in components
        and normalized.endswith(".py")
    )


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

    1. 終端状態: 終端オプション。当該区間は後続の指定を実行しないため走査を終える（状態`terminal`）
    2. 値あり状態: 値ありオプション表と完全一致する。トークンと続く1トークンを走査対象から除く。
       `--name=value`形式は`--name`が同表と完全一致する場合に1トークンだけを除く
    3. 値なし状態: 値なしオプション表と完全一致する。トークン1つを除く
    4. 非オプション状態: `-`で始まらない。当該トークンを走査の到達点とする（状態`reached`）
    5. 未分類状態: 上記のいずれにも当たらない（表に無い長形、2文字以上の結合短縮形、表に無い短縮形など）。
       区間全体を実行位置未確定とする（状態`unresolved`）

    5状態は排他かつ網羅であり、優先順位が固定されているため同じトークンが2つの状態へ当たることはない。
    値なしオプション表に`--help`・`-h`が含まれていても、終端状態を最優先で判定するため状態1で確定する。
    新しいオプションや未知の記法が現れても個別の規則追加を要さず状態5へ倒れ、助言用検査は非検出となる。
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


@dataclasses.dataclass(frozen=True)
class GitEvent:
    """Bashコマンド内の1回のgit呼び出しを表す。

    属性:

    - `subcommand`: gitのサブコマンド名（`log`・`commit`・`rebase`・`push`等）。
      サブコマンドに到達せずグローバルオプションのみで終わる場合は空文字列。
    - `cwd`: そのgit呼び出しの実効作業ディレクトリ。`cd`・`pushd`・`git -C`の
      効果を反映した、解決済みのパスを保持する。解決不能な場合は空文字列。
    - `cwd_resolved`: `cwd`が実効作業ディレクトリとして解決済みなら真。
      `cd`・`pushd`・`git -C`の引数にシェル展開が含まれる場合や、初期cwdが不明な場合は偽。
      解決不能なイベントでは、消費側がpayloadのcwdへ戻って状態を参照しない。
    - `unresolved_expression`: cwdを解決不能にした式。式以外の理由で解決不能な場合と
      解決済みの場合は`None`。
    - `global_options`: サブコマンド前に出現したgitのグローバルオプションのトークン列。
    - `subcommand_args`: サブコマンド名以降のトークン列。
    """

    subcommand: str
    cwd: str
    global_options: list[str]
    subcommand_args: list[str]
    cwd_resolved: bool = True
    unresolved_expression: str | None = None


def split_bash_segments(command: str) -> list[str]:
    """Bashコマンドを`;`・`&&`・`||`・`|`・`&`で分割する。

    クォート（`'`・`"`）内のメタ文字は分割対象外とする。
    バックスラッシュエスケープやheredocは厳密に扱わないため、heredocを含む
    コマンドは呼び出し側で除外する想定。
    """
    segments: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
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
        if c in ("&", "|") and i + 1 < len(command) and command[i + 1] == c:
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if c in (";", "&", "|"):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def extract_git_events(command: str, payload_cwd: str) -> list[GitEvent]:
    """Bashコマンドからgit呼び出しイベント列を抽出する。

    `payload_cwd`を初期cwdとして`split_bash_segments`の結果を順に評価する。
    `cd`・`pushd`が先頭にあるセグメントでは現在cwdを更新する。
    `popd`はスタックを静的に追跡できないためcwdを解決不能にする。
    先頭が`git`のセグメントでは`GitEvent`を1件記録する。
    その他のコマンドは現在cwdに影響を与えない。

    `shlex.split`で解釈不能なセグメント（クォート閉じ忘れ等）は無視する。
    """
    events: list[GitEvent] = []
    current_cwd = CwdResolution(payload_cwd, bool(payload_cwd))
    for segment in split_bash_segments(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        start = _skip_env_assignments(tokens, 0)
        if start >= len(tokens):
            continue
        head = tokens[start]
        cwd_change = resolve_cwd_change(tokens, current_cwd)
        if cwd_change is not None:
            current_cwd = cwd_change
            continue
        if head == "git":
            event = _parse_git_call(tokens[start:], current_cwd)
            if event is not None:
                events.append(event)
    return events


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


def _parse_git_call(tokens: list[str], current_cwd: CwdResolution) -> GitEvent | None:
    """`git ...`形式のトークン列を解析してGitEventを返す。

    `tokens[0]`は`git`である前提。グローバルオプションを順次解釈して`-C`の効果を
    実効cwdへ反映し、最初に登場したオプション以外のトークンをサブコマンドとして扱う。
    サブコマンドに到達せず終わった場合は`subcommand`が空文字列のGitEventを返す。
    未知のオプション・形式は中断してその時点のGitEventを返す。
    """
    if not tokens or tokens[0] != "git":
        return None
    global_options: list[str] = []
    effective_cwd = current_cwd
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--") and "=" in token:
            key, _, value = token.partition("=")
            if key in _GLOBAL_OPTIONS_WITH_VALUE:
                global_options.append(token)
                # `--git-dir` / `--work-tree` 等は実効cwdに直接影響しないため値を記録するのみ。
                i += 1
                continue
            if key in _GLOBAL_OPTIONS_WITHOUT_VALUE:
                # 値を持たないはずのオプションに`=`が付く場合は未知扱い。
                break
            break
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            if i + 1 >= len(tokens):
                break
            value = tokens[i + 1]
            global_options.append(token)
            global_options.append(value)
            if token == "-C":
                effective_cwd = _normalize_relative(value, effective_cwd)
            i += 2
            continue
        if token in _GLOBAL_OPTIONS_WITHOUT_VALUE:
            global_options.append(token)
            i += 1
            continue
        # サブコマンド到達。
        return GitEvent(
            subcommand=token,
            cwd=effective_cwd.path,
            global_options=global_options,
            subcommand_args=list(tokens[i + 1 :]),
            cwd_resolved=effective_cwd.resolved,
            unresolved_expression=effective_cwd.unresolved_expression,
        )
    return GitEvent(
        subcommand="",
        cwd=effective_cwd.path,
        global_options=global_options,
        subcommand_args=[],
        cwd_resolved=effective_cwd.resolved,
        unresolved_expression=effective_cwd.unresolved_expression,
    )
