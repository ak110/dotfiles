"""古いレーンの確実に取込み済みの成果を判定する。"""

import pathlib
import typing

from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._git import command as git_command


def _verbatim_patches(repo: pathlib.Path, common: str, tip: str) -> dict[bytes, bytes]:
    span = f"{common}..{tip}"
    commits = _lane_records.git(repo, "rev-list", "--no-merges", "--max-count=512", span).splitlines()
    if len(commits) >= 512:
        raise ValueError("パッチの取込みを確かめる対象が512commit以上あるため回収しない")
    patch = git_command.run(
        [
            "log",
            "--no-merges",
            "--format=commit %H",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--full-index",
            "--no-renames",
            "--binary",
            "-p",
            span,
        ],
        repo,
        check=True,
        capture_output=True,
        timeout=_lane_records.TIMEOUT,
    ).stdout
    identities = git_command.run(
        ["patch-id", "--verbatim"],
        repo,
        input=patch,
        check=True,
        capture_output=True,
        timeout=_lane_records.TIMEOUT,
    ).stdout
    rows = [line.split() for line in identities.splitlines()]
    if any(len(row) != 2 for row in rows):
        raise ValueError("パッチの一致結果を解釈できないため回収しない")
    if not set(commits) <= {commit.decode("ascii") for _, commit in rows}:
        raise ValueError("パッチを確認できないcommitを含むため回収しない")
    return dict(rows)


def prove(record: dict[str, typing.Any], repo: pathlib.Path) -> dict[str, str]:
    """選定が残っていても、別の仕事を含まない古いbranchだけを認める。"""
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
    if _lane_records.git(repo, "rev-list", "--merges", f"{base_head}..{head}"):
        raise ValueError("統合先へ含まれないmerge commitがあるため回収しない")
    common = _lane_records.git(repo, "merge-base", head, base_head)
    lane_patches = _verbatim_patches(repo, common, head)
    base_patches = _verbatim_patches(repo, common, base_head)
    if not lane_patches or not lane_patches.keys() <= base_patches.keys():
        raise ValueError("統合先へ含まれない変更があるため回収しない")
    return {"head": head, "base_head": base_head, "basis": "equivalent-patch"}
