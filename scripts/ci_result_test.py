"""必須checkの成功・失敗・意図した省略をCLIの終了コードで検証する。"""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ci_owner
import pytest
import yaml
from agent_toolkit._testing.git_repository import commit_all, init_repository, run_git


@pytest.mark.parametrize("owns", ["true", "false", ""])
@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_required_check_result(
    tmp_path: Path,
    host_environ: Callable[[], dict[str, str]],
    owns: str,
    result: str,
) -> None:
    environment = {
        **host_environ(),
        "OWNS": owns,
        "OWNER_REASON": "同期pushの非所有run",
        "NEEDS": json.dumps({"common-ci-owner": {"result": "success"}, "check": {"result": result}}),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("ci_result.py"))],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=10,
    )
    expected = owns == "false" or (owns == "true" and result == "success")
    assert process.returncode == (0 if expected else 1), process.stderr
    if owns == "false":
        assert environment["OWNER_REASON"] in process.stdout


def _workflow_job_names() -> set[str]:
    root = Path(ci_owner.__file__).resolve().parents[1]
    workflow = yaml.load((root / ".github/workflows/ci.yaml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    names = set()
    for job_id, job in workflow["jobs"].items():
        name = job.get("name", job_id)
        if expanded := re.search(r"\|\| '([^']+)'", name):
            names.add(expanded[1])
        elif formatted := re.search(r"\|\| format\('([^']+)', matrix\.(\w+)\)", name):
            values = job["strategy"]["matrix"][formatted[2]]
            if isinstance(values, str):
                owner_values = re.search(r"&& '(\[[^']+\])' \|\|", values)
                assert owner_values is not None, values
                values = json.loads(owner_values[1])
            names.update(formatted[1].format(value) for value in values)
        elif job_id == "statusline-build":
            names.update(f"statusline-build ({entry['os']})" for entry in job["strategy"]["matrix"]["include"])
        else:
            names.add(name)
    return names


@pytest.mark.parametrize(
    "evidence",
    [
        "complete",
        "missing",
        "skipped",
        "neutral",
        "non-owner",
        "step-skipped",
        "missing-pylint-runtime",
        "missing-pylint-remaining",
        "missing-pylint-cross-module",
        "failed-pylint-cross-module",
    ],
)
def test_master_reuses_only_a_complete_owner_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence: str) -> None:
    project, second = "example/dotfiles", "develop-sha"
    run = {
        "id": 21,
        "run_attempt": 2,
        "status": "completed",
        "conclusion": "success",
        "head_sha": second,
        "head_branch": "develop",
        "event": "push",
        "head_repository": {"full_name": project},
        "html_url": "https://example.org/actions/runs/21",
    }
    jobs: list[dict[str, Any]] = [
        {"name": name, "status": "completed", "conclusion": "success", "steps": []} for name in _workflow_job_names()
    ]
    selected = {
        "browser-e2e": "ブラウザーE2Eの実行",
        "rust-lint": "Rust検証の実行",
    }
    for job in jobs:
        if job["name"].startswith("statusline-build ("):
            job["conclusion"] = "skipped"
        if step := selected.get(job["name"]):
            job["steps"] = [{"name": step, "conclusion": "success"}]
    unix_job = next(job for job in jobs if job["name"] == "test-linux")
    if evidence == "missing":
        jobs.remove(unix_job)
    elif evidence in {"skipped", "neutral"}:
        unix_job["conclusion"] = evidence
    elif evidence == "non-owner":
        unix_job["name"] = "test-linux (non-owner)"
    elif evidence == "step-skipped":
        next(job for job in jobs if job["name"] == "browser-e2e")["steps"][0]["conclusion"] = "skipped"
    elif evidence.startswith("missing-pylint-"):
        name = f"pylint (3.15) ({evidence.removeprefix('missing-pylint-')})"
        jobs.remove(next(job for job in jobs if job["name"] == name))
    elif evidence == "failed-pylint-cross-module":
        next(job for job in jobs if job["name"] == "pylint (3.15) (cross-module)")["conclusion"] = "failure"

    def pages(path: str, key: str) -> list[dict[str, Any]]:
        if key == "workflow_runs":
            assert path.startswith(f"repos/{project}/actions/workflows/ci.yaml/runs?branch=develop&event=push&")
            assert f"head_sha={second}" in path
            return [run]
        assert path == f"repos/{project}/actions/runs/21/attempts/2/jobs?per_page=100"
        return jobs

    monkeypatch.setattr(ci_owner, "_paginated", pages)
    monkeypatch.setattr(ci_owner, "_second_parent_for_reuse", lambda _sha: second)
    output = _owner_input(tmp_path, monkeypatch, "master-sha")
    ci_owner.main()
    assert output.read_text(encoding="utf-8").splitlines()[0] == f"owns={str(evidence != 'complete').lower()}"


def _owner_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sha: str) -> Path:
    output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("RELEASE_PULL_REQUEST", "false")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/master")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/dotfiles")
    monkeypatch.setenv("GITHUB_SHA", sha)
    return output


@pytest.mark.parametrize("case", ["same-tree", "single-parent", "different-tree", "not-ancestor"])
def test_master_ci_reuse_requires_a_same_tree_two_parent_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    repository = init_repository(tmp_path / "repo")
    monkeypatch.chdir(repository)
    (repository / "initial").write_text("start\n", encoding="utf-8")
    commit_all(repository, "initial")
    base = run_git(repository, "branch", "--show-current").stdout.strip()
    run_git(repository, "switch", "-c", "for-ci")
    (repository / "tracked").write_text("develop\n", encoding="utf-8")
    commit_all(repository, "develop")
    second = run_git(repository, "rev-parse", "HEAD").stdout.strip()
    if case != "single-parent":
        run_git(repository, "switch", base)
        if case == "not-ancestor":
            (repository / "other").write_text("master\n", encoding="utf-8")
            commit_all(repository, "other")
        run_git(repository, "merge", "--no-ff", "--no-commit", "for-ci")
        if case == "different-tree":
            (repository / "other").write_text("merge-only\n", encoding="utf-8")
        commit_all(repository, "merge")
    queried = []

    def runs(_repository: str, _branch: str, sha: str) -> list[dict[str, Any]]:
        queried.append(sha)
        return []

    output = _owner_input(tmp_path, monkeypatch, run_git(repository, "rev-parse", "HEAD").stdout.strip())
    monkeypatch.setattr(ci_owner, "_matching_push_runs", runs)
    ci_owner.main()
    assert output.read_text(encoding="utf-8").splitlines()[0] == "owns=true"
    assert queried == ([second] if case == "same-tree" else [])
