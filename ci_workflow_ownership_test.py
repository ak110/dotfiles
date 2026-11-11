"""CI workflowの主題別契約を検証する。"""

import io
import json
import os
import runpy
import subprocess
import sys
import typing
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from _test_helpers import (
    _JUNIT_UPLOAD,
    _OWNS,
    REPO_ROOT,
    _run_git,
    _statusline_job,
    expanded_workflow_steps,
    workflow_jobs,
    workflow_mapping,
    workflow_step_source,
    workflow_steps,
)
from _test_helpers import (
    _workflow_fixture as _workflow_data_fixture,  # noqa: F401  # pytestがfixtureとして収集する。
)

_RELEASE_PULL_REQUEST_CONDITIONS = (
    "github.event_name == 'pull_request'",
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.head_ref == 'develop'",
    "github.base_ref == 'master'",
)


def _common_job_names(workflow: dict[str, object]) -> list[str]:
    """release pull requestで非所有の表示名を持つ共通jobの名前を返す。"""
    return [name for name, job in workflow_jobs(workflow).items() if "(non-owner)" in str(workflow_mapping(job)["name"])]


def test_common_jobs_share_owner_output(workflow_data: dict[str, object]) -> None:
    """共通jobの全実処理が先行jobの所有出力を参照する。"""
    common_jobs = _common_job_names(workflow_data)
    assert set(common_jobs) == {
        "test-linux",
        "test-windows",
        "update-dotfiles-upgrade-windows",
        "python-lint",
        "pylint",
        "pytest-root",
        "pytest-toolkit",
        "pytest-toolkit-result",
        "python-lint-result",
        "browser-e2e",
        "rust-lint",
    }
    owner = workflow_mapping(workflow_jobs(workflow_data)["common-ci-owner"])
    assert owner["permissions"] == {"actions": "read", "contents": "read"}
    outputs = workflow_mapping(owner["outputs"])
    assert outputs["owns"] == "${{ steps.owner.outputs.owns }}"
    assert outputs["reason"] == "${{ steps.owner.outputs.reason }}"
    judgment = next(step for step in workflow_steps(owner) if step.get("id") == "owner")
    assert judgment["id"] == "owner"
    release_expression = str(workflow_mapping(judgment["env"])["RELEASE_PULL_REQUEST"])
    assert release_expression == "${{ " + " && ".join(_RELEASE_PULL_REQUEST_CONDITIONS) + " }}"
    assert str(workflow_steps(owner)[0]["uses"]).startswith("actions/checkout@")
    for job_name in common_jobs:
        job = workflow_mapping(workflow_jobs(workflow_data)[job_name])
        assert "needs.common-ci-owner.outputs.owns == 'false'" in str(job["name"])
        steps = workflow_steps(job)
        if job_name.endswith("-result"):
            continue
        marker, *real = steps
        assert marker["if"] == "needs.common-ci-owner.outputs.owns == 'false'"
        assert workflow_mapping(marker["env"])["OWNER_REASON"] == "${{ needs.common-ci-owner.outputs.reason }}"
        if job_name in {"rust-lint", "browser-e2e"}:
            assert job["if"] == "${{ !cancelled() }}"
            assert real[-1]["if"] == "steps.selection.outputs.selected == 'true'"
        else:
            assert "if" not in job
            # JUnit XMLの保存だけは、pytestの失敗後も実行するため所有条件へ継続条件を加える。
            for step in real:
                uploads = "upload-artifact@" in str(step.get("uses", ""))
                assert step.get("if") == (_JUNIT_UPLOAD if uploads else _OWNS), step

    version = _statusline_job(workflow_data)
    assert "needs" not in version
    assert version["if"] == (
        "(github.event_name == 'pull_request' && github.base_ref == 'master') || "
        "(github.event_name == 'push' && github.ref == 'refs/heads/develop')"
    )
    assert workflow_data["concurrency"] == {
        "group": "ci-${{ github.event_name }}-${{ github.ref }}",
        "cancel-in-progress": "true",
    }


class _WorkflowRunsResponse(io.BytesIO):
    headers: typing.ClassVar[dict[str, str]] = {}


@pytest.mark.parametrize(
    ("event", "branch", "release_pr", "run_state", "expected_owns"),
    [
        ("push", "develop", False, "queued", False),
        ("push", "develop", False, "in_progress", False),
        ("push", "develop", False, "success", False),
        ("push", "develop", False, "failure", True),
        ("push", "develop", False, "cancelled", True),
        ("push", "develop", False, "none", True),
        ("push", "develop", False, "api-error", True),
        ("push", "develop", False, "wrong-sha", True),
        ("push", "develop", False, "wrong-repo", True),
        ("push", "develop", False, "wrong-branch", True),
        ("push", "develop", False, "wrong-event", True),
        ("push", "master", False, "success", True),
        ("push", "feature", False, "success", True),
        ("pull_request", "develop", True, "none", False),
        ("pull_request", "feature", False, "none", True),
        ("pull_request", "develop", False, "none", True),
    ],
)
def test_common_ci_owner_runs_workflow_judgment(
    workflow_data: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event: str,
    branch: str,
    *,
    release_pr: bool,
    run_state: str,
    expected_owns: bool,
) -> None:
    """実判定へREST応答を与え、同期push・通常push・PR・照会失敗の出力を確かめる。

    応答fieldはGitHub REST list-workflow-runs-for-a-workflowの公開schemaに従う。
    """
    job = workflow_mapping(workflow_jobs(workflow_data)["common-ci-owner"])
    step = next(step for step in workflow_steps(job) if step.get("id") == "owner")
    script = workflow_step_source(step)
    script_path = tmp_path / "owner.py"
    script_path.write_text(script, encoding="utf-8")
    output = tmp_path / "output"
    for key, value in {
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": f"refs/heads/{branch}",
        "GITHUB_REPOSITORY": "ak110/dotfiles",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_API_URL": "https://api.github.com",
        "GH_TOKEN": "test-token",
        "RELEASE_PULL_REQUEST": str(release_pr).lower(),
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    requests: list[str] = []

    def urlopen(request: urllib.request.Request, **_kwargs: object) -> io.BytesIO:
        requests.append(request.full_url)
        parsed = urllib.parse.urlsplit(request.full_url)
        assert parsed.path == "/repos/ak110/dotfiles/actions/workflows/ci.yaml/runs"
        assert urllib.parse.parse_qs(parsed.query) == {
            "branch": ["master"],
            "event": ["push"],
            "head_sha": ["a" * 40],
            "per_page": ["100"],
        }
        if run_state == "api-error":
            raise urllib.error.URLError("API unavailable")
        run = {
            "head_sha": "b" * 40 if run_state == "wrong-sha" else "a" * 40,
            "head_branch": "feature" if run_state == "wrong-branch" else "master",
            "head_repository": {"full_name": "other/repo" if run_state == "wrong-repo" else "ak110/dotfiles"},
            "event": "pull_request" if run_state == "wrong-event" else "push",
            "status": run_state if run_state in {"queued", "in_progress"} else "completed",
            "conclusion": run_state if run_state in {"success", "failure", "cancelled"} else "success",
            "html_url": "https://github.com/ak110/dotfiles/actions/runs/123",
        }
        return _WorkflowRunsResponse(json.dumps({"workflow_runs": [] if run_state == "none" else [run]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    runpy.run_path(str(script_path), run_name="__main__")
    if run_state == "api-error":
        diagnostic = capsys.readouterr().out
        assert "このrunで共通検証を実行します" in diagnostic
        assert "::warning::" not in diagnostic
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert values["owns"] == str(expected_owns).lower()
    assert bool(requests) == (event == "push" and branch == "develop")
    if not expected_owns:
        assert ("develop branch" if release_pr else "/actions/runs/123") in values["reason"]


@pytest.mark.parametrize(
    ("event", "conclusion", "branch", "repository", "eligible"),
    [
        ("push", "success", "master", "ak110/dotfiles", True),
        ("push", "success", "develop", "ak110/dotfiles", False),
        ("push", "failure", "master", "ak110/dotfiles", False),
        ("pull_request", "success", "master", "ak110/dotfiles", False),
        ("push", "success", "master", "other/repo", False),
    ],
)
def test_common_ci_release_boundary(
    tmp_path: Path,
    event: str,
    conclusion: str,
    branch: str,
    repository: str,
    *,
    eligible: bool,
) -> None:
    """Releaseの実gateは同一repositoryのmaster push成功だけを受理する。"""
    release = workflow_mapping(
        yaml.load(
            (REPO_ROOT / ".github/workflows/release-statusline.yaml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
    )
    jobs = workflow_jobs(release)
    gate = expanded_workflow_steps(workflow_mapping(jobs["gate"]))[0]
    assert workflow_mapping(gate["env"])["CONCLUSION"] == "${{ github.event.workflow_run.conclusion }}"
    output = tmp_path / "gate-output"
    result = subprocess.run(
        ["bash"],
        input=str(gate["run"]),
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
            "EVENT": event,
            "CONCLUSION": conclusion,
            "HEAD_BRANCH": branch,
            "HEAD_REPOSITORY": repository,
            "REPOSITORY": "ak110/dotfiles",
            "HEAD_SHA": "a" * 40,
            "GITHUB_OUTPUT": str(output),
        },
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert values["eligible"] == str(eligible).lower()
    prepare = workflow_mapping(jobs["prepare"])
    assert prepare["if"] == "needs.gate.outputs.eligible == 'true'"
    assert workflow_mapping(expanded_workflow_steps(prepare)[0]["with"])["ref"] == "${{ needs.gate.outputs.head_sha }}"


@pytest.mark.parametrize(
    ("changed_path", "event", "branch", "mode", "expected"),
    [
        ("docs/development/note.md", "push", "feature", "normal", (False, False)),
        ("agent-toolkit/skills/workflow-overview/SKILL.md", "push", "feature", "normal", (False, False)),
        ("rust/claude-statusline/src/main.rs", "push", "feature", "normal", (True, False)),
        ("rust/claude-statusline/fixtures/input.json", "push", "feature", "normal", (True, False)),
        ("agent-toolkit/agent_toolkit/_atk/serve/static/wi.js", "push", "feature", "normal", (False, True)),
        ("conftest.py", "push", "feature", "normal", (False, True)),
        ("agent-toolkit/agent_toolkit/_testing/serve_support.py", "push", "feature", "normal", (False, True)),
        ("pyproject.toml", "push", "feature", "normal", (True, True)),
        (".github/workflows/ci.yaml", "push", "feature", "normal", (True, True)),
        ("mise.toml", "push", "feature", "normal", (True, True)),
        ("future/input.bin", "push", "feature", "normal", (True, True)),
        ("docs/development/note.md", "push", "feature", "missing-history", (True, True)),
        ("docs/development/note.md", "push", "feature", "first", (True, True)),
        ("docs/development/note.md", "push", "develop", "normal", (True, True)),
        ("docs/development/note.md", "push", "master", "normal", (True, True)),
        ("docs/development/note.md", "pull_request", "feature", "pr-base", (False, False)),
        ("docs/development/note.md", "pull_request", "feature", "missing-pr-base", (True, True)),
        ("docs/development/note.md", "push", "feature", "branch-history", (True, False)),
        ("docs/development/note.md", "push", "feature", "rename", (True, False)),
        ("docs/a\ninjected=true.md", "push", "feature", "normal", (False, False)),
        ("README.md", "push", "feature", "empty", (True, True)),
    ],
)
def test_ci_scope_runs_real_workflow_selection(
    workflow_data: dict[str, object],
    tmp_path: Path,
    host_environ: Callable[[], dict[str, str]],
    changed_path: str,
    event: str,
    branch: str,
    mode: str,
    expected: tuple[bool, bool],
) -> None:
    """実Gitの共通祖先と実workflowの判定から、検査領域と省略理由を取得する。"""
    repository = tmp_path / "repository"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=develop")
    _run_git(repository, "config", "user.email", "ci@example.com")
    _run_git(repository, "config", "user.name", "CI")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    if mode == "rename":
        (repository / "rust").mkdir()
        (repository / "rust/input.rs").write_text("input\n", encoding="utf-8")
    _run_git(repository, "add", ".")
    _run_git(repository, "commit", "-m", "base")
    base = _run_git(repository, "rev-parse", "HEAD")
    if branch != "develop":
        _run_git(repository, "switch", "-c", branch)
    before = base
    if mode == "branch-history":
        (repository / "rust").mkdir()
        (repository / "rust/input.rs").write_text("input\n", encoding="utf-8")
        _run_git(repository, "add", ".")
        _run_git(repository, "commit", "-m", "earlier Rust change")
        before = _run_git(repository, "rev-parse", "HEAD")
    if mode != "empty":
        target = repository / changed_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "rename":
            _run_git(repository, "mv", "rust/input.rs", changed_path)
        else:
            target.write_text("change\n", encoding="utf-8")
        _run_git(repository, "add", ".")
        _run_git(repository, "commit", "-m", "change")
    head = _run_git(repository, "rev-parse", "HEAD")
    if mode != "missing-history":
        _run_git(repository, "update-ref", "refs/remotes/origin/develop", head if mode == "pr-base" else base)
    owner = workflow_mapping(workflow_jobs(workflow_data)["common-ci-owner"])
    step = next(step for step in expanded_workflow_steps(owner) if step.get("id") == "scope")
    assert workflow_mapping(step["env"])["PR_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    script = workflow_step_source(step)
    script_path = tmp_path / "scope.py"
    script_path.write_text(script, encoding="utf-8")
    output = tmp_path / "output"
    host = host_environ()
    environment: dict[str, str] = {key: host[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT") if key in host}
    environment.update(
        {
            "HOME": str(tmp_path / "home"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "EVENT_NAME": event,
            "REF_NAME": f"refs/heads/{branch}",
            "CURRENT_SHA": head,
            "PR_BASE_SHA": "" if mode == "missing-pr-base" else base,
            "BEFORE_SHA": "0" * 40 if mode == "first" else before,
            "GITHUB_OUTPUT": str(output),
        }
    )
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert set(values) == {"rust", "browser", "scope_reason"}
    assert values["rust"] in {"true", "false"} and values["browser"] in {"true", "false"}
    assert (values["rust"] == "true", values["browser"] == "true") == expected
    assert values["scope_reason"] and values["scope_reason"] in result.stdout
    print(
        json.dumps(
            {"path": changed_path, "mode": mode, "selection": values, "previous_owned_run": [True, True]}, ensure_ascii=True
        )
    )


@pytest.mark.parametrize("job_name", ["rust-lint", "browser-e2e"])
@pytest.mark.parametrize(
    ("owns", "owner_result", "selected", "expected_exit"),
    [
        ("true", "success", "true", 0),
        ("true", "success", "false", 0),
        ("true", "failure", "true", 1),
        ("true", "success", "", 1),
        ("", "failure", "", 1),
        ("false", "success", "", 0),
    ],
)
def test_ci_scope_failure_is_not_an_intentional_skip(
    workflow_data: dict[str, object],
    tmp_path: Path,
    host_environ: Callable[[], dict[str, str]],
    job_name: str,
    owns: str,
    owner_result: str,
    selected: str,
    expected_exit: int,
) -> None:
    """選択しない成功と、判定失敗・出力欠落による必須checkの失敗を区別する。"""
    job = workflow_mapping(workflow_jobs(workflow_data)[job_name])
    assert job["if"] == "${{ !cancelled() }}"
    step = next(step for step in expanded_workflow_steps(job) if step.get("name") == "検査領域の判定を確認")
    assert "if" not in step
    environment = workflow_mapping(step["env"])
    flag = "rust" if job_name == "rust-lint" else "browser"
    assert environment["SELECTED"] == f"${{{{ needs.common-ci-owner.outputs.{flag} }}}}"
    runtime = host_environ()
    child_environment: dict[str, str] = {
        key: runtime[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "PATHEXT") if key in runtime
    }
    child_environment.update(
        {
            "OWNS": owns,
            "OWNER_RESULT": owner_result,
            "SELECTED": selected,
            "CHECK_NAME": job_name,
            "SCOPE_REASON": "文書だけの変更",
            "GITHUB_OUTPUT": str(tmp_path / "output"),
        }
    )
    result = subprocess.run(
        ["bash"],
        input=workflow_step_source(step),
        cwd=tmp_path,
        env=child_environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    if owns == "true" and expected_exit == 0:
        assert f"実行={selected}" in result.stdout and "文書だけの変更" in result.stdout
