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


def run_git_lines(args: list[str], cwd: str) -> list[str] | None:
    """gitコマンドを実行し、出力を行リストで返す。失敗時はNoneを返す。

    `pretooluse.py`・`posttooluse.py`が共有するgit実行ヘルパー。
    `pretooluse.py`内に同名で存在していたプライベート関数`_run_git_lines`を
    本モジュールへ移設し、公開名（アンダースコア無し）とした。
    """
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, cwd=cwd, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def list_remotes(cwd: str) -> list[str]:
    """構成済みリモート名の一覧を取得する。取得失敗時・リモート未構成時は空リストを返す。"""
    lines = run_git_lines(["git", "remote"], cwd)
    return lines if lines is not None else []


def resolve_default_branch(cwd: str) -> str | None:
    """構成済みリモートのHEAD参照から既定ブランチ名を解決する。

    各リモートについて`git symbolic-ref --short refs/remotes/<remote>/HEAD`を実行し、
    最初に成功した値（例: `origin/master`）を返す。全リモートで解決に失敗した場合はNoneを返す。
    上流ブランチの追跡先が`gone`（削除済み・未設定）の環境で`@{u}`が解決できない場合の
    フォールバック比較先として`pretooluse.py`の版数bump検知フックが使用する。
    """
    for remote in list_remotes(cwd):
        lines = run_git_lines(["git", "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], cwd)
        if lines:
            return lines[0]
    return None


def snapshot_remote_refs(cwd: str) -> dict[str, dict[str, str] | None]:
    """全リモートのref名とOIDのスナップショットを取得する。

    戻り値は`{<remote>: {<ref名>: <OID>} | None}`の辞書。キーは`git remote`で取得した
    構成済みリモート名全件を含む。`git ls-remote`が失敗したリモートは値を`None`とする
    （取得失敗のマーカーであり、リモート名自体は保持する）。値を単純に欠落させず`None`で
    残すのは、比較時に「取得失敗（既知のリモートだが値が無い）」と「新規追加されたリモート
    （その時点で未知だったリモート）」を区別できるようにするためである（「[16]機械チェックの
    実装設計」の誤検知抑止条件を参照。取得失敗を「参照が消えた」という差分と誤認しない）。
    """
    snapshot: dict[str, dict[str, str] | None] = {}
    for remote in list_remotes(cwd):
        lines = run_git_lines(["git", "ls-remote", "--heads", "--tags", remote], cwd)
        if lines is None:
            snapshot[remote] = None
            continue
        refs: dict[str, str] = {}
        for line in lines:
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            oid, ref = parts
            refs[ref] = oid
        snapshot[remote] = refs
    return snapshot
