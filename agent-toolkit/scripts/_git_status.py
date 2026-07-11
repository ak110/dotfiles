"""Claude Code agent-toolkit: Git作業ツリー状態確認の共有ヘルパー。

`git status --porcelain`実行と追跡ファイル変更判定を集約する。
`pretooluse.py`・`posttooluse.py`・`stop_advisor.py`が同一の判定ロジック・共有定数を消費する形に統一している。
"""

from __future__ import annotations

import subprocess

# `git status --porcelain`実行のタイムアウト秒数。
_STATUS_TIMEOUT = 10

# git commit --amend / --fixup 成功時に設定するセッション状態フラグ名（cwd別辞書として管理する）。
# `pretooluse.py`・`posttooluse.py`双方が同一キーで参照する共有SSOT。
AMEND_PENDING_FLAG_KEY = "amend_pending_status_check"


def git_push_is_real_send(args: list[str]) -> bool:
    """`git push`のサブコマンド引数列から`--dry-run`/`-n`未指定の実送出pushを判定する。"""
    return "--dry-run" not in args and "-n" not in args


def is_tracked_change(line: str) -> bool:
    """Git status --porcelain / --shortの1行が追跡ファイルの変更行かどうかを返す。

    未追跡ファイル（`??`）は対象外とする。
    """
    return bool(line) and not line.startswith("??")


def get_status_porcelain(cwd: str) -> str | None:
    """`git -C <cwd> status --porcelain`の標準出力を返す。

    `cwd`未指定・実行失敗・タイムアウト時はNoneを返す。
    """
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def has_tracked_dirty(cwd: str) -> bool | None:
    """作業ツリーに追跡ファイルの未コミット差分があるかを判定する。

    未追跡ファイル（`??`行）は除外する。`cwd`未指定・実行失敗時はNoneを返す。
    """
    output = get_status_porcelain(cwd)
    if output is None:
        return None
    return any(is_tracked_change(line) for line in output.splitlines())
