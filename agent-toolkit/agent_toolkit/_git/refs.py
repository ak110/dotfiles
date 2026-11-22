import dataclasses
import pathlib
from collections.abc import Callable

type GitCapture = Callable[[pathlib.Path, list[str]], str | None]


@dataclasses.dataclass(frozen=True)
class BranchRef:
    ref: str
    branch: str
    remote: str | None = None


def resolve_branch(local_path: pathlib.Path, *, git_fn: GitCapture, upstream: bool = False) -> BranchRef | None:
    if not upstream:
        ref = git_fn(local_path, ["symbolic-ref", "HEAD"]) or ""
        prefix = "refs/heads/"
        return BranchRef(ref, ref[len(prefix) :]) if ref.startswith(prefix) else None
    ref = git_fn(local_path, ["rev-parse", "--symbolic-full-name", "@{u}"])
    if not ref:
        ref = git_fn(local_path, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if not ref:
        return None
    remotes = (git_fn(local_path, ["remote"]) or "").splitlines()
    remote = max((name for name in remotes if ref.startswith(f"refs/remotes/{name}/")), key=len, default=None)
    if remote is None:
        return None
    return BranchRef(ref, ref[len(f"refs/remotes/{remote}/") :], remote)
