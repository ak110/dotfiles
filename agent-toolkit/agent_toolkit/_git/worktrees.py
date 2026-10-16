"""Gitのworktree登録を、Gitの版によらない方法で列挙する。

`git worktree list --porcelain -z`はGit 2.36で追加された引数であり、agent-toolkitが対応するGit 2.34.1では
終了129になる。`-z`を使わない出力は改行を含むパスの境界を区別できない。
そこでGitが文書化する保存形式（共通Gitディレクトリの`worktrees/<id>/`配下の`gitdir`・`HEAD`・`locked`）を読み、
`git worktree list`と同じ対応（パス、branch、detached、bare、locked、prunable）を復元する。
Gitの起動は共通Gitディレクトリと`core.bare`の取得に限り、どちらもGit 2.34.1で受理される引数を使う。
"""

import dataclasses
import pathlib
import subprocess

from agent_toolkit._git import command as git_command

_SYMBOLIC_REF_PREFIX = "ref: "
_BRANCH_PREFIX = "refs/heads/"


class WorktreeListError(ValueError):
    """worktree登録を列挙できなかったことを表す。"""


@dataclasses.dataclass(frozen=True)
class Worktree:
    """1件のworktree登録。

    `path`はGitが登録した作業ツリーのパス（解決前）である。`branch`は`refs/heads/`を除いたbranch名で、
    detached HEAD・bare・`refs/heads/`以外を指すHEADでは`None`となる。`head_ref`はHEADが指す参照の完全名で、
    detached HEADとbareでは`None`となる。
    """

    path: pathlib.Path
    branch: str | None
    head_ref: str | None
    detached: bool
    bare: bool
    locked: bool
    prunable: bool


def _git(repo: pathlib.Path, args: list[str], timeout: float | None) -> str:
    try:
        result = git_command.run(args, repo, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorktreeListError(f"git {args[0]}を実行できない: {error}") from error
    if result.returncode:
        raise WorktreeListError(f"git {args[0]}が終了{result.returncode}: {result.stderr.strip()}")
    return result.stdout


def _read_text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None


def _strip_git_suffix(value: str) -> str:
    # Gitは登録パスの末尾の`/.git`を除いて作業ツリーのパスとする（worktree.cのget_main_worktree・get_linked_worktree）。
    for suffix in ("/.git", "/."):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _head(gitdir: pathlib.Path) -> tuple[str | None, bool]:
    """HEADが指す参照の完全名と、detached HEADかどうかを返す。"""
    content = _read_text(gitdir / "HEAD")
    if content is None:
        return None, False
    value = content.strip()
    if value.startswith(_SYMBOLIC_REF_PREFIX):
        return value.removeprefix(_SYMBOLIC_REF_PREFIX).strip(), False
    return None, bool(value)


def _entry(path: pathlib.Path, gitdir: pathlib.Path, *, bare: bool, locked: bool, prunable: bool) -> Worktree:
    head_ref, detached = (None, False) if bare else _head(gitdir)
    branch = head_ref.removeprefix(_BRANCH_PREFIX) if head_ref and head_ref.startswith(_BRANCH_PREFIX) else None
    return Worktree(path=path, branch=branch, head_ref=head_ref, detached=detached, bare=bare, locked=locked, prunable=prunable)


def list_worktrees(repo: pathlib.Path, *, timeout: float | None = None) -> list[Worktree]:
    """`repo`が属するGitリポジトリのworktree登録を、主作業ツリー、登録識別子の順に返す。

    `repo`は主作業ツリー、linked worktree、bareリポジトリのいずれでもよい。
    Gitが登録を読めずに一覧から外す項目（`gitdir`を読めないか空の登録）は返さない。
    """
    common = pathlib.Path(_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"], timeout).rstrip("\n"))
    bare_value = _git(repo, ["config", "--bool", "--default", "false", "core.bare"], timeout).strip()
    main_path = pathlib.Path(_strip_git_suffix(str(common)))
    entries = [_entry(main_path, common, bare=bare_value == "true", locked=False, prunable=False)]
    registry = common / "worktrees"
    try:
        identifiers = sorted(child for child in registry.iterdir() if child.is_dir()) if registry.is_dir() else []
    except OSError as error:
        raise WorktreeListError(f"worktree登録を読めない: {registry}: {error}") from error
    for gitdir in identifiers:
        # Gitは`gitdir`の末尾の空白だけを除く（strbuf_rtrim）。パスの途中の改行は保持する。
        location = (_read_text(gitdir / "gitdir") or "").rstrip()
        if not location:
            continue
        locked = (gitdir / "locked").exists()
        # Git 2.34.1の`should_prune_worktree`と同じく、locked以外で登録先の`.git`が無い項目をprunableとする。
        prunable = not locked and not pathlib.Path(location).exists()
        entries.append(_entry(pathlib.Path(_strip_git_suffix(location)), gitdir, bare=False, locked=locked, prunable=prunable))
    return entries
