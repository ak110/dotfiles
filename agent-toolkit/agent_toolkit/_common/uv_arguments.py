"""`uv`と`python`の起動の引数を解析し、実際に実行される位置とagent-toolkitのスクリプトの起動かを判定する処理。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

_PYTHON_TOKEN_PATTERN = re.compile(r"^python[0-9.]*(?:\.exe)?$", re.IGNORECASE)


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


# uv 0.13.1の`uvx --help`。globalの定義を共有し、tool runの引数の個数を保持する。
_UV_TOOL_OPTIONS_WITH_VALUE = _UV_GLOBAL_OPTIONS_WITH_VALUE | {
    "--from",
    "--with",
    "-w",
    "--with-editable",
    "--with-requirements",
    "--constraints",
    "-c",
    "--build-constraints",
    "-b",
    "--overrides",
    "--env-file",
    "--python-platform",
    "--torch-backend",
    "--index",
    "--default-index",
    "--index-url",
    "-i",
    "--extra-index-url",
    "--find-links",
    "-f",
    "--index-strategy",
    "--keyring-provider",
    "--upgrade-package",
    "-P",
    "--upgrade-group",
    "--resolution",
    "--prerelease",
    "--prerelease-package",
    "--fork-strategy",
    "--exclude-newer",
    "--exclude-newer-package",
    "--no-sources-package",
    "--reinstall-package",
    "--link-mode",
    "--config-setting",
    "-C",
    "--config-settings-package",
    "--no-build-isolation-package",
    "--no-build-package",
    "--no-binary-package",
    "--refresh-package",
    "--python",
    "-p",
}
_UV_TOOL_OPTIONS_WITHOUT_VALUE = _UV_GLOBAL_OPTIONS_WITHOUT_VALUE | {
    "--isolated",
    "--no-env-file",
    "--lfs",
    "--no-index",
    "--upgrade",
    "-U",
    "--no-sources",
    "--reinstall",
    "--compile-bytecode",
    "--no-build-isolation",
    "--no-build",
    "--no-binary",
    "--require-build-hashes",
    "--no-require-build-hashes",
    "--refresh",
}


@dataclass(frozen=True)
class RuntimeInvocation:
    """argvから確定した実行位置・起動方式と、uvによるcwd変更。"""

    tokens: tuple[str, ...]
    kind: Literal["command", "module", "script", "code", "terminal", "unresolved"] = "command"
    directories: tuple[str, ...] = ()


def executable_name(token: str) -> str:
    """実行位置の名前から、実行ファイル用の拡張子だけを除く。"""
    return PurePath(token).name.removesuffix(".exe").removesuffix(".cmd")


def _python_invocation(tokens: list[str], directories: tuple[str, ...]) -> RuntimeInvocation:
    """Pythonのオプション値を消費し、module・script・非実行を区別する。"""
    index = 1
    while index < len(tokens):
        word = tokens[index]
        if word in {"--help", "--version", "--help-env", "--help-xoptions", "--help-all"}:
            return RuntimeInvocation((), "terminal", directories)
        if word == "--":
            return RuntimeInvocation(tuple(tokens[index + 1 :]), "script", directories)
        if word == "-" or not word.startswith("-"):
            return RuntimeInvocation(tuple(tokens[index:]), "script", directories)
        if word.startswith("--check-hash-based-pycs="):
            index += 1
            continue
        if word == "--check-hash-based-pycs":
            if index + 1 >= len(tokens):
                return RuntimeInvocation((), "unresolved", directories)
            index += 2
            continue
        if word.startswith("--"):
            return RuntimeInvocation((), "unresolved", directories)
        for offset, option in enumerate(word[1:], 2):
            if option in "h?V":
                return RuntimeInvocation((), "terminal", directories)
            if option == "c":
                return RuntimeInvocation(tuple(tokens[index:]), "code", directories)
            if option in "mWX":
                attached = word[offset:]
                value = attached or (tokens[index + 1] if index + 1 < len(tokens) else None)
                if value is None:
                    return RuntimeInvocation((), "unresolved", directories)
                following = index + (1 if attached else 2)
                if option == "m":
                    return RuntimeInvocation((value, *tokens[following:]), "module", directories)
                index = following - 1
                break
            if option not in "bBdEiIOPqsSuvx":
                return RuntimeInvocation((), "unresolved", directories)
        index += 1
    return RuntimeInvocation(("-",), "script", directories)


def _uv_option_values(tokens: list[str], start: int, end: int, values: frozenset[str]) -> list[tuple[str, str | None]]:
    """検証済みのオプション区間から、値と結合短縮形を区別して返す。"""
    options: list[tuple[str, str | None]] = []
    index = start
    while index < end:
        word = tokens[index]
        if word == "--":
            break
        name, equal, value = word.partition("=")
        if name in values:
            if not equal:
                index += 1
                value = tokens[index]
            options.append((name, value))
        elif word.startswith("-") and not word.startswith("--"):
            for offset, letter in enumerate(word[1:], 2):
                option = "-" + letter
                if option in values:
                    attached = word[offset:]
                    if not attached:
                        index += 1
                    options.append((option, attached or tokens[index]))
                    break
                options.append((option, None))
        else:
            options.append((name, None))
        index += 1
    return options


def resolve_runtime_invocation(tokens: Sequence[str]) -> RuntimeInvocation:
    """shellへ再評価せず、uv・uvx・Pythonを通した実行位置と起動方式を返す。

    値付きオプションは実行名にせず、未知構文はunresolved、非対象の起動はその実行名で返す。
    従来の助言用解析とは別に、起動方式の確定を要する消費側が明示して使う。
    """
    argv = list(tokens)
    directories: tuple[str, ...] = ()
    while argv:
        name = executable_name(argv[0])
        if is_python_token(name):
            return _python_invocation(argv, directories)
        if name not in {"uv", "uvx"}:
            return RuntimeInvocation(tuple(argv), "command", directories)
        tool_mode = name == "uvx"
        if tool_mode:
            start = 1
        else:
            subcommand, state = _scan_uv_options(
                argv, 1, _UV_GLOBAL_OPTIONS_WITH_VALUE, _UV_GLOBAL_OPTIONS_WITHOUT_VALUE, expanded=True
            )
            if state == "terminal":
                return RuntimeInvocation((), "terminal", directories)
            if state != "reached":
                return RuntimeInvocation((), "unresolved", directories)
            tool_mode = argv[subcommand : subcommand + 2] == ["tool", "run"]
            if not tool_mode and argv[subcommand] != "run":
                return RuntimeInvocation(tuple(argv), "command", directories)
            start = subcommand + (2 if tool_mode else 1)
        values = _UV_TOOL_OPTIONS_WITH_VALUE if tool_mode else _UV_RUN_OPTIONS_WITH_VALUE
        flags = (
            _UV_TOOL_OPTIONS_WITHOUT_VALUE
            if tool_mode
            else _UV_RUN_OPTIONS_WITHOUT_VALUE | {"--require-build-hashes", "--no-require-build-hashes"}
        )
        index, state = _scan_uv_options(argv, start, values, flags, expanded=True)
        if state == "terminal":
            return RuntimeInvocation((), "terminal", directories)
        if state != "reached":
            return RuntimeInvocation((), "unresolved", directories)
        directories += tuple(
            value
            for name, value in _uv_option_values(argv, 1, index, values | _UV_GLOBAL_OPTIONS_WITH_VALUE)
            if name == "--directory" and value is not None
        )
        options = {name for name, _value in _uv_option_values(argv, start, index, values)}
        argv = argv[index:]
        if not tool_mode and options.intersection({"-m", "--module"}):
            return RuntimeInvocation(tuple(argv), "module", directories)
        if not tool_mode and options.intersection({"-s", "--script", "--gui-script"}):
            return RuntimeInvocation(tuple(argv), "script", directories)
        if tool_mode and "@" in argv[0]:
            argv[0] = argv[0].partition("@")[0]
    return RuntimeInvocation((), "unresolved", directories)


def is_python_token(token: str) -> bool:
    """`python`・`python3`・`python3.12`などの実行ファイル名なら真を返す。"""
    return _PYTHON_TOKEN_PATTERN.match(token) is not None


def has_uv_terminal_option(tokens: Sequence[str]) -> bool:
    """トークン列に`uv`の終端オプションが含まれる場合に真を返す。"""
    return any(token in _UV_TERMINAL_OPTIONS for token in tokens)


def resolve_uv_execution_index(tokens: list[str], uv_index: int) -> int | None:
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


def is_agent_toolkit_script_invocation(tokens: Sequence[str], uv_index: int, execution_index: int) -> bool:
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
    *,
    expanded: bool = False,
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
        if expanded and token == "-":
            return index, "reached"
        if expanded and token == "--":
            return (index + 1, "reached") if index + 1 < len(tokens) else (index, "unresolved")
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
        if expanded and token.startswith("-") and not token.startswith("--"):
            consumed = 1
            for offset, letter in enumerate(token[1:], 2):
                option = "-" + letter
                if option in _UV_TERMINAL_OPTIONS:
                    return index, "terminal"
                if option in with_value:
                    consumed = 1 if token[offset:] else 2
                    break
                if option not in without_value:
                    return index, "unresolved"
            index += consumed
            continue
        if not token.startswith("-"):
            return index, "reached"
        return index, "unresolved"
    return index, "unresolved"
