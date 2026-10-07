"""Claude Code agent-toolkit: Git作業ツリー状態確認の共有ヘルパー。"""

from __future__ import annotations

from agent_toolkit._git import command as _git_command

# `git status --porcelain`実行のタイムアウト秒数。
_STATUS_TIMEOUT = 10


def get_status_porcelain(cwd: str) -> str | None:
    """`git -C <cwd> status --porcelain`の標準出力を返す。

    `cwd`未指定・実行失敗・タイムアウト時はNoneを返す。
    """
    if not cwd:
        return None
    return _git_command.optional_stdout(["-C", cwd, "status", "--porcelain"], timeout=_STATUS_TIMEOUT)


def run_git_lines(args: list[str], cwd: str) -> list[str] | None:
    """`cwd`を作業ディレクトリとしてgitを実行し、空行を除いた出力の行リストを返す。失敗時はNoneを返す。"""
    output = _git_command.lines(args, cwd)
    if output is None:
        return None
    return [line for line in output if line.strip()]
