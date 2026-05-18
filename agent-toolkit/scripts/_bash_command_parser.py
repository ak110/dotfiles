"""Claude Code agent-toolkit: Bashコマンド内のgit呼び出しイベントを抽出するヘルパー。

`;`・`&&`・`||`・`|`・`&`で区切られたセグメントを順に評価し、`cd`・`pushd`で
現在ディレクトリを追跡しながら、各git呼び出しごとに`GitEvent`を返す。
`git -C <dir>`の相対パスは出現時点の現在cwdを基点に正規化する。

`pretooluse` / `posttooluse`の両方が同一のイベント列を消費する形に統一している。
"""

from __future__ import annotations

import dataclasses
import os
import os.path
import re
import shlex

_ENV_ASSIGN_PATTERN = re.compile(r"^[A-Za-z_]\w*=")

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


@dataclasses.dataclass(frozen=True)
class GitEvent:
    """Bashコマンド内の1回のgit呼び出しを表す。

    属性:

    - `subcommand`: gitのサブコマンド名（`log`・`commit`・`rebase`・`push`等）。
      サブコマンドに到達せずグローバルオプションのみで終わる場合は空文字列。
    - `cwd`: そのgit呼び出しの実効作業ディレクトリ。`cd`・`pushd`・`git -C`の
      効果を反映したパスを保持する。`payload_cwd`が空かつcdが無い場合は空文字列。
    - `global_options`: サブコマンド前に出現したgitのグローバルオプションのトークン列。
    - `subcommand_args`: サブコマンド名以降のトークン列。
    """

    subcommand: str
    cwd: str
    global_options: list[str]
    subcommand_args: list[str]


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
    `cd`・`pushd`が先頭にあるセグメントでは現在cwdを更新する（`popd`は厳密スタックを
    持たず無視する）。先頭が`git`のセグメントでは`GitEvent`を1件記録する。
    その他のコマンドは現在cwdに影響を与えない。

    `shlex.split`で解釈不能なセグメント（クォート閉じ忘れ等）は無視する。
    """
    events: list[GitEvent] = []
    current_cwd = payload_cwd or ""
    for segment in split_bash_segments(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        start = _skip_env_assignments(tokens, 0)
        if start >= len(tokens):
            continue
        head = tokens[start]
        if head in ("cd", "pushd"):
            current_cwd = _apply_cd(tokens, start, current_cwd)
            continue
        if head == "popd":
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


def _apply_cd(tokens: list[str], start: int, current_cwd: str) -> str:
    """`cd`・`pushd`の引数を解釈して新しい現在cwdを返す。

    引数なし・オプション（`-`等）・解析不能時は現在cwdを変更しない。
    相対パスは現在cwd基点で`os.path.normpath`正規化する。
    """
    if start + 1 >= len(tokens):
        return current_cwd
    target = tokens[start + 1]
    if not target or target.startswith("-"):
        return current_cwd
    return _normalize_relative(target, current_cwd)


def _normalize_relative(target: str, current_cwd: str) -> str:
    """相対パスを現在cwd基点で正規化する。絶対パスはそのまま正規化する。"""
    if os.path.isabs(target):
        return os.path.normpath(target)
    if current_cwd:
        return os.path.normpath(os.path.join(current_cwd, target))
    return os.path.normpath(target)


def _parse_git_call(tokens: list[str], current_cwd: str) -> GitEvent | None:
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
            cwd=effective_cwd,
            global_options=global_options,
            subcommand_args=list(tokens[i + 1 :]),
        )
    return GitEvent(
        subcommand="",
        cwd=effective_cwd,
        global_options=global_options,
        subcommand_args=[],
    )
