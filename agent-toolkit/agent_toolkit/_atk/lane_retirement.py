import pathlib
import typing

from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._git import command as git_command


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


def orphan_candidates(repo: pathlib.Path, registered: list[dict[str, typing.Any]]) -> list[dict[str, str | None]]:
    common = _lane_records.common(repo)
    records = [item["record"] for item in registered if item["record"].get("common_dir") == common]
    owned_paths = {str(pathlib.Path(item["worktree"]).resolve()) for item in records}
    owned_branches = {item["branch"] for item in records}
    trees = _lane_records.worktrees(repo)
    main = _lane_records.main_worktree(repo).resolve()

    def named(branch: str | None) -> bool:
        return bool(branch and branch.startswith(("orch/", "lead/", "process-wi-", "worktree-")))

    results: list[dict[str, str | None]] = []
    reason = "所有登録がなく作成者を確認できない。ユーザー確認なしに削除しない"
    for path, branch in sorted(trees.items()):
        resolved = pathlib.Path(path).resolve()
        if resolved in {main, repo.resolve()} or str(resolved) in owned_paths:
            continue
        if named(branch) or "managed-temp" in resolved.parts or "worktrees" in resolved.parts:
            results.append({"worktree": str(resolved), "branch": branch or None, "reason": reason})
    linked = set(trees.values())
    for branch in sorted(_lane_records.branches(repo)):
        if branch not in owned_branches and branch not in linked and named(branch):
            results.append({"worktree": None, "branch": branch, "reason": reason})
    return results
