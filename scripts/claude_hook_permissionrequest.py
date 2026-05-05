#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""Claude Code PermissionRequestフック: 信頼領域への書き込みを自動許可する。

PreToolUseの`permissionDecision: "allow"`は組み込みのaskルール
（`.claude/`配下の編集確認ダイアログ等）を上書きできないため、
確認ダイアログ表示時に発火するPermissionRequestフックで自動許可を返す。

自動許可の対象パス:

1. Gitワークツリー配下の`.claude/`配下（`~/.claude/`配下を除く）
2. `~/.claude/plans/`配下

各判定ロジックの詳細は対応する関数のdocstringを参照する。
予期せぬ例外は0にフォールバックする（フックが破損して編集できなくなる事故を避けるため）。
"""

import json
import pathlib
import shlex
import sys
import traceback

# Git ワークツリー判定で親ディレクトリを遡る際の上限段数。
# 病的に深いパスでの暴走を防ぐガード。
_GIT_WORKTREE_LOOKUP_DEPTH = 64

_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

# Bash 自動許可の対象コマンド（引数がすべて対象パス配下なら許可）。
# 危険性が高い `dd` / `tar` / `rsync` 等は対象外。
_BASH_FILE_OPS = frozenset({"rm", "mkdir", "mv", "cp", "touch", "ln", "chmod", "chown"})

# 安全と判断できないシェルメタ文字。`>` `>>` のリダイレクトのみ別途トークンレベルで許容する。
_UNSAFE_METACHARS = frozenset("|&;`$()<")


def _main() -> int:
    """エントリポイント。exit code 0 を返す。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0

    if tool_name in _FILE_TOOLS:
        file_path_raw = tool_input.get("file_path")
        file_path = file_path_raw if isinstance(file_path_raw, str) else ""
        allowed = should_allow(file_path)
    elif tool_name == "Bash":
        command_raw = tool_input.get("command")
        command = command_raw if isinstance(command_raw, str) else ""
        cwd_raw = payload.get("cwd")
        cwd = cwd_raw if isinstance(cwd_raw, str) else ""
        allowed = should_allow_bash(command, cwd)
    else:
        return 0

    if not allowed:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"},
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


def should_allow(file_path: str) -> bool:
    """単一ファイルパスが自動許可対象か判定する。

    `~/.claude/plans/` 配下、または Git ワークツリー配下の `.claude/` 配下のいずれかに
    該当する場合 True を返す。
    """
    target = _normalize_path(file_path)
    if target is None:
        return False
    return _is_target_path(target)


def should_allow_bash(command: str, cwd: str) -> bool:
    """Bash コマンドの操作対象パスがすべて自動許可対象配下なら True を返す。

    安全に解析できないコマンド (危険メタ文字含む / shlex 失敗 / 対象外コマンド) は False。
    """
    if not command:
        return False
    if any(ch in _UNSAFE_METACHARS for ch in command):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False

    redirect_targets, remaining = _split_redirects(tokens)
    if remaining is None:
        return False  # 不正なリダイレクト構文のため拒否する

    paths: list[str] = list(redirect_targets)
    if remaining:
        cmd = remaining[0]
        if cmd in _BASH_FILE_OPS:
            paths.extend(_extract_path_args(remaining[1:]))
        elif not redirect_targets:
            # ファイル操作系でもリダイレクトでもないため拒否する。
            return False
        # else: リダイレクトのみで判定する（コマンド本体は問わない）

    if not paths:
        return False

    cwd_base = _resolve_cwd(cwd)
    for path_arg in paths:
        target = _normalize_path(path_arg, cwd_base=cwd_base)
        if target is None or not _is_target_path(target):
            return False
    return True


def _normalize_path(file_path: str, *, cwd_base: pathlib.Path | None = None) -> pathlib.Path | None:
    """パス文字列を正規化された絶対パスへ変換する（失敗時は None）。

    相対パスは `cwd_base` 起点で絶対パスへ解決する。`cwd_base` が None の場合は
    相対パスを拒否する。`~` は expanduser で展開する。
    """
    if not file_path:
        return None
    try:
        target = pathlib.Path(file_path).expanduser()
        if not target.is_absolute():
            if cwd_base is None:
                return None
            target = cwd_base / target
        # `.` / `..`・シンボリックリンクを解消して字句比較の迂回を防ぐ。
        # strict=False で存在しないパスでも例外を送出しない。
        return target.resolve(strict=False)
    except (ValueError, OSError):
        return None


def _resolve_cwd(cwd: str) -> pathlib.Path | None:
    """`cwd` 文字列を絶対パスへ正規化する（空 / 不正なら None）。"""
    if not cwd:
        return None
    try:
        path = pathlib.Path(cwd).expanduser()
        if not path.is_absolute():
            return None
        return path.resolve(strict=False)
    except (ValueError, OSError):
        return None


def _is_target_path(target: pathlib.Path) -> bool:
    """正規化済みパスが自動許可対象配下か判定する。"""
    try:
        home_claude = (pathlib.Path.home() / ".claude").resolve(strict=False)
    except (ValueError, OSError):
        return False
    if _is_under(target, home_claude / "plans"):
        return True
    return _is_repo_claude_edit(target, home_claude)


def _is_under(target: pathlib.Path, base: pathlib.Path) -> bool:
    """`target` が `base` 配下（`base` 自身は含まない）か判定する。"""
    try:
        rel = target.relative_to(base)
    except ValueError:
        return False
    return bool(rel.parts)


def _is_repo_claude_edit(target: pathlib.Path, home_claude: pathlib.Path) -> bool:
    """Git ワークツリー配下の `.claude/` 配下への編集か判定する。

    `~/.claude/` 配下は除外する (配布先誤編集の警告経路を維持するため)。
    """
    if ".claude" not in target.parts:
        return False
    try:
        target.relative_to(home_claude)
        return False
    except ValueError:
        pass
    return _is_inside_git_worktree(target)


def _is_inside_git_worktree(target: pathlib.Path) -> bool:
    """対象パスの親を遡って `.git` の存在を確認する。

    `.git` はディレクトリ（通常のリポジトリ）またはファイル（worktree / submodule）の
    いずれもありうるため `exists()` で判定する。subprocess は使わずファイルシステム
    存在確認のみで完結させる（PermissionRequest が編集毎に実行されるため軽量化が必要）。
    """
    current = target.parent
    for _ in range(_GIT_WORKTREE_LOOKUP_DEPTH):
        if (current / ".git").exists():
            return True
        if current.parent == current:
            return False
        current = current.parent
    return False


def _split_redirects(tokens: list[str]) -> tuple[list[str], list[str] | None]:
    """`>` / `>>` トークンを抽出してリダイレクト先パスと残りのトークンに分ける。

    リダイレクト直後にトークンがない不正な構文の場合は `(_, None)` を返す。
    """
    redirects: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in (">", ">>"):
            if i + 1 >= len(tokens):
                return [], None
            redirects.append(tokens[i + 1])
            i += 2
            continue
        remaining.append(token)
        i += 1
    return redirects, remaining


def _extract_path_args(args: list[str]) -> list[str]:
    """`-` で始まらない引数のみをパスとして抽出する。

    `--` をオプション終端マーカーとして扱う（それ以降は `-` 始まりもすべてパス扱い）。
    """
    paths: list[str] = []
    after_double_dash = False
    for arg in args:
        if not after_double_dash:
            if arg == "--":
                after_double_dash = True
                continue
            if arg.startswith("-"):
                continue
        paths.append(arg)
    return paths


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        traceback.print_exc()
        sys.exit(0)
