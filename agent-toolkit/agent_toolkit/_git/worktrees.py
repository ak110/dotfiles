import dataclasses
import pathlib
import subprocess
from collections.abc import Iterable

from agent_toolkit._common import path_ownership
from agent_toolkit._git import command as git_command

_SYMBOLIC_REF_PREFIX = "ref: "
_BRANCH_PREFIX = "refs/heads/"


class WorktreeListError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Worktree:
    path: pathlib.Path
    branch: str | None
    head_ref: str | None
    detached: bool
    bare: bool
    locked: bool
    prunable: bool


def owning_worktree(path: pathlib.Path, entries: Iterable[Worktree]) -> Worktree | None:
    return owning_resolved_worktree(path, resolve_worktrees(entries))


def resolve_worktrees(entries: Iterable[Worktree]) -> dict[pathlib.Path, Worktree]:
    return {entry.path.resolve(): entry for entry in entries}


def owning_resolved_worktree(path: pathlib.Path, resolved: dict[pathlib.Path, Worktree]) -> Worktree | None:
    root = path_ownership.owning_resolved_root(path, resolved)
    return resolved.get(root) if root is not None else None


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

    for suffix in ("/.git", "/."):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def main_worktree_path(common_dir: str) -> pathlib.Path:
    r"""`git rev-parse --git-common-dir`が返すGit形式の文字列から、主作業ツリーのパスを返す。

    接尾辞の`/.git`はGit形式（区切り文字`/`）の文字列から除く。WindowsでPathへ変換してから文字列へ戻すと区切り文字が`\`になり、
    `/.git`と一致しなくなって`.git`が残るためである。
    """
    return pathlib.Path(_strip_git_suffix(common_dir))


def _head(gitdir: pathlib.Path) -> tuple[str | None, bool]:
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
    common_value = _git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"], timeout).rstrip("\n")
    common = pathlib.Path(common_value)
    bare_value = _git(repo, ["config", "--bool", "--default", "false", "core.bare"], timeout).strip()
    main_path = main_worktree_path(common_value)
    entries = [_entry(main_path, common, bare=bare_value == "true", locked=False, prunable=False)]
    registry = common / "worktrees"
    try:
        identifiers = sorted(child for child in registry.iterdir() if child.is_dir()) if registry.is_dir() else []
    except OSError as error:
        raise WorktreeListError(f"worktree登録を読めない: {registry}: {error}") from error
    for gitdir in identifiers:
        location = (_read_text(gitdir / "gitdir") or "").rstrip()
        if not location:
            continue
        locked = (gitdir / "locked").exists()

        prunable = not locked and not pathlib.Path(location).exists()
        entries.append(_entry(pathlib.Path(_strip_git_suffix(location)), gitdir, bare=False, locked=locked, prunable=prunable))
    return entries
