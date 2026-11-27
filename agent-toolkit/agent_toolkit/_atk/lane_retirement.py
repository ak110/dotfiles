import json
import pathlib
import typing

from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk.managed_temp import paths as temp_paths
from agent_toolkit._common import state_paths
from agent_toolkit._git import command as git_command
from agent_toolkit._git import worktrees as git_worktrees


def prove(record: dict[str, typing.Any], repo: pathlib.Path) -> dict[str, str]:
    if _lane_records.common(repo) != record["common_dir"]:
        raise ValueError("repoのGit共通ディレクトリが所有記録と異なる")
    branch = f"refs/heads/{record['branch']}"
    base = f"refs/heads/{record['base_branch']}"
    if branch == base:
        raise ValueError("統合先branchは回収しない")
    head = _lane_records.git(repo, "rev-parse", "--verify", branch)
    base_head = _lane_records.git(repo, "rev-parse", "--verify", base)
    if head == record.get("initial_head"):
        raise ValueError("開始HEADから進んでいないため、選定未完了の作業先を回収しない")
    _lane_records.git(repo, "merge-base", "--is-ancestor", record["initial_head"], head)
    integration = record.get("integration")
    if isinstance(integration, dict) and integration.get("phase") != "finalized":
        raise ValueError("統合の最終化が未完了のため回収しない")
    return prove_heads(repo, head, base_head)


def prove_heads(repo: pathlib.Path, head: str, base_head: str) -> dict[str, str]:
    reached = git_command.run(
        ["merge-base", "--is-ancestor", head, base_head],
        repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=_lane_records.TIMEOUT,
    )
    if reached.returncode == 0:
        return {"head": head, "base_head": base_head, "basis": "ancestor"}
    if reached.returncode != 1:
        raise ValueError(f"git merge-baseが終了{reached.returncode}: {reached.stderr.strip()}")

    lane_tree = _lane_records.git(repo, "rev-parse", f"{head}^{{tree}}")
    base_tree = _lane_records.git(repo, "rev-parse", f"{base_head}^{{tree}}")
    if lane_tree != base_tree:
        raise ValueError("統合先へ含まれない変更があるか、完全な内容一致を確認できないため回収しない")
    return {"head": head, "base_head": base_head, "basis": "identical-tree"}


def integration_worktrees(common: str) -> set[str]:
    root = state_paths.state_dir() / "process-loop-ownership"
    paths: set[str] = set()
    for path in root.glob("*.json"):
        if path.name.endswith(".result.json"):
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("common_dir") != common:
            continue
        manifest = record.get("manifest")
        manifest_path = pathlib.Path(manifest) if manifest else None
        for key in ("worktree", "prepared_worktree", "registered_worktree"):
            if record.get(key):
                paths.add(str(pathlib.Path(record[key]).resolve()))
        if manifest_path is not None and manifest_path.is_file():
            for entry in json.loads(manifest_path.read_text(encoding="utf-8"))["repositories"]:
                if entry.get("common_dir") == common:
                    for key in ("worktree", "registered_worktree"):
                        if entry.get(key):
                            paths.add(str(pathlib.Path(entry[key]).resolve()))
    return paths


def upstream_branches(repo: pathlib.Path) -> set[str]:
    return {
        ref.removeprefix("refs/heads/")
        for line in _lane_records.git(repo, "for-each-ref", "--format=%(refname)%00%(upstream)", "refs/heads").splitlines()
        for ref, _, upstream in [line.partition("\0")]
        if upstream
    }


def symbolic_branches(repo: pathlib.Path) -> set[str]:
    return {
        ref
        for line in _lane_records.git(repo, "for-each-ref", "--format=%(refname)%00%(symref)", "refs/heads").splitlines()
        for ref, _, target in [line.partition("\0")]
        if target
    }


def operation_branches(repo: pathlib.Path) -> set[str]:
    common = pathlib.Path(_lane_records.common(repo))
    branches: set[str] = set()
    try:
        linked = list((common / "worktrees").iterdir())
    except FileNotFoundError:
        linked = []
    for gitdir in (common, *(path for path in linked if path.is_dir())):
        for name in ("rebase-apply/head-name", "rebase-merge/head-name", "BISECT_START", "rebase-merge/update-refs"):
            try:
                lines = (gitdir / name).read_text(encoding="utf-8", errors="surrogateescape").splitlines()
            except FileNotFoundError:
                continue
            if name == "rebase-merge/update-refs":
                branches.update(lines[::3])
            elif lines and lines[0] != "detached HEAD":
                branches.add(lines[0] if lines[0].startswith("refs/") else f"refs/heads/{lines[0]}")
    return branches


def orphan_candidates(
    repo: pathlib.Path, registered: list[dict[str, typing.Any]], *, caller: pathlib.Path | None = None
) -> list[dict[str, str | None]]:
    common = _lane_records.common(repo)
    records = [item["record"] for item in registered if item["record"].get("common_dir") == common]
    owned_paths = {str(pathlib.Path(item["worktree"]).resolve()) for item in records}
    owned_branches = {item["branch"] for item in records}
    entries = git_worktrees.list_worktrees(repo, timeout=_lane_records.TIMEOUT)
    if entries[0].bare:
        raise ValueError("repoが主作業ツリーを持たない")
    trees = {str(entry.path.resolve()): entry.branch or "" for entry in entries}
    locked = {str(entry.path.resolve()) for entry in entries if entry.locked}
    main = entries[0].path.resolve()
    protected = integration_worktrees(common) | {str(main), str(repo.resolve())}
    if caller is not None:
        protected.update(path for path in trees if caller.resolve().is_relative_to(path))
    upstream = upstream_branches(repo)
    symbolic = symbolic_branches(repo)
    managed_root = temp_paths.temp_root_path().resolve()
    nested_paths = {parent for path in set(trees) | protected | owned_paths for parent in pathlib.Path(path).parents}

    def named(branch: str | None) -> bool:
        return bool(branch and branch.startswith(("orch/", "lead/", "process-wi-", "worktree-")))

    def candidate(path: str | None, branch: str | None) -> dict[str, str | None]:
        parts = pathlib.Path(path).parts if path else ()
        placed = bool(path and pathlib.Path(path).is_relative_to(managed_root) and pathlib.Path(path) != managed_root)
        placed |= (".claude", "worktrees") in zip(parts, parts[1:], strict=False)
        origin = "agent" if named(branch) or placed else "unknown"
        reason = None
        if path in protected or branch == "worktree-process-loop":
            reason = "主・呼出元・統合先のworktreeまたはbranchを回収しない"
        elif path in locked:
            reason = "Gitでロックされたworktreeは回収しない"
        elif path is not None and pathlib.Path(path) in nested_paths:
            reason = "別のworktreeを内包するため回収しない"
        elif f"refs/heads/{branch}" in symbolic:
            reason = "シンボリックrefを回収しない"
        elif branch in upstream:
            reason = "upstreamを設定したbranchを回収しない"
        elif origin == "unknown":
            reason = "作成者を確認できない。ユーザー確認なしに削除しない"
        return {"worktree": path, "branch": branch, "origin": origin, "reason": reason}

    return [
        candidate(path, branch or None)
        for path, branch in sorted(trees.items())
        if path not in owned_paths and branch not in owned_branches and path not in {str(main), str(repo.resolve())}
    ] + [candidate(None, branch) for branch in sorted(set(_lane_records.branches(repo)) - owned_branches - set(trees.values()))]
