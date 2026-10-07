"""テストが使うGitリポジトリの作成とGitコマンドの実行を提供する。

commitの作者とGitのglobal・system設定の隔離は`isolation.py`が環境変数で与えるため、本モジュールは設定しない。
`git_fakes.py`は`subprocess`の偽装であり、実物のGitリポジトリを作成する本モジュールとは別の責務である。
"""

import pathlib
import subprocess
from collections.abc import Mapping

# 一時ディレクトリの小さなリポジトリへの操作は数秒で終わる。停止した場合にテスト全体の上限より先に原因を報告させる。
_TIMEOUT_SECONDS = 60


def run_git(
    repository: pathlib.Path,
    *arguments: str,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`repository`を作業ディレクトリとしてGitを実行し、標準出力と標準エラーを文字列で返す。"""
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=None if env is None else dict(env),
        timeout=_TIMEOUT_SECONDS,
    )


def git_output(repository: pathlib.Path, *arguments: str, env: Mapping[str, str] | None = None) -> str:
    """Gitを実行し、前後の空白を除いた標準出力を返す。"""
    return run_git(repository, *arguments, env=env).stdout.strip()


def init_repository(
    path: pathlib.Path,
    *,
    initial_branch: str | None = None,
    origin: str | None = None,
    files: Mapping[str, str] | None = None,
    commit_message: str | None = None,
    env: Mapping[str, str] | None = None,
) -> pathlib.Path:
    """作業ツリーを持つGitリポジトリを作成して`path`を返す。

    `initial_branch`を省略すると、branch名はGitの`init.defaultBranch`の解決に従う。`origin`はremoteの`origin`へ登録するURLかパス。
    `files`は作業ツリーへ書くファイル（相対パスと本文）。`commit_message`を指定すると、作業ツリーの全ファイルを
    最初のcommitへ記録する（ファイルが無い場合は空のcommit）。`env`は最初のcommitの環境（作成日時の指定など）。
    """
    path.mkdir(parents=True, exist_ok=True)
    branch_arguments = () if initial_branch is None else (f"--initial-branch={initial_branch}",)
    run_git(path, "init", "--quiet", *branch_arguments)
    if origin is not None:
        run_git(path, "remote", "add", "origin", origin)
    for relative, content in (files or {}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if commit_message is not None:
        commit_all(path, commit_message, env=env)
    return path


def init_bare_repository(path: pathlib.Path, *, initial_branch: str = "main") -> pathlib.Path:
    """remoteとして使うbareリポジトリを作成して`path`を返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    run_git(path.parent, "init", "--quiet", "--bare", f"--initial-branch={initial_branch}", str(path))
    return path


def commit_all(repository: pathlib.Path, message: str, *, env: Mapping[str, str] | None = None) -> str:
    """作業ツリーの全ての変更を1つのcommitへ記録し、そのcommitのOIDを返す。変更が無い場合は空のcommitを作成する。"""
    run_git(repository, "add", "-A", env=env)
    run_git(repository, "commit", "--quiet", "--allow-empty", "-m", message, env=env)
    return git_output(repository, "rev-parse", "HEAD")
