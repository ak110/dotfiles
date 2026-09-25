"""Claude Code agent-toolkit: Git作業ツリー状態確認の共有ヘルパー。"""

from __future__ import annotations

import subprocess

from agent_toolkit._git import command as _git_command

# `git status --porcelain`実行のタイムアウト秒数。
_STATUS_TIMEOUT = 10


def _run_git(cwd: str, *arguments: str) -> str | None:
    """`git -C <cwd> <arguments>`の標準出力を返す。

    `cwd`未指定・終了コード非0・実行失敗・タイムアウト時はNoneを返す。
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def get_status_porcelain(cwd: str) -> str | None:
    """`git -C <cwd> status --porcelain`の標準出力を返す。

    `cwd`未指定・実行失敗・タイムアウト時はNoneを返す。
    """
    return _run_git(cwd, "status", "--porcelain")


def run_git_lines(args: list[str], cwd: str) -> list[str] | None:
    """gitコマンドを実行し、出力を行リストで返す。失敗時はNoneを返す。"""
    command_args = args[1:] if args and args[0] == "git" else args
    output = _git_command.lines(command_args, cwd)
    if output is None:
        return None
    return [line for line in output if line.strip()]
