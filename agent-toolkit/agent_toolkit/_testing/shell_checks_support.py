import json
import pathlib
import subprocess

import pytest

from agent_toolkit._hooks import work_contract
from agent_toolkit._hooks.pretooluse import shell_checks
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.pretooluse_support import (
    _plan_file_state_env,
    _run,
    run_without_skill_notices,
)


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """シェル固有の無通知契約を、作業契約を作成済みの状態で検証する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: シェル操作の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


def _run_in_ready_context(payload: dict, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return run_without_skill_notices(payload, env_overrides=env_overrides)


def _denial_reason(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0
    assert not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    reason = specific["permissionDecisionReason"]
    assert reason
    return reason


def _run_git_commit_message_hook(
    repo: pathlib.Path, tmp_path: pathlib.Path, command: str, extra_payload: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    head_before = git_repository.git_output(repo, "rev-parse", "HEAD")
    status_before = git_repository.git_output(repo, "status", "--porcelain=v1")
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo), **extra_payload}
    env = _plan_file_state_env(tmp_path)

    env["AGENT_TOOLKIT_CONFIG_REPOSITORIES"] = json.dumps({str(repo): {"edit": "allow"}})
    result = _run(payload, env_overrides=env)
    observed = {
        "payload": payload,
        "shell_checks_source": shell_checks.__file__,
        "head_before": head_before,
        "head_after": git_repository.git_output(repo, "rev-parse", "HEAD"),
        "status_before": status_before,
        "status_after": git_repository.git_output(repo, "status", "--porcelain=v1"),
        "inside_worktree": git_repository.git_output(repo, "rev-parse", "--is-inside-work-tree"),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    (tmp_path / "public-hook-observation.json").write_text(json.dumps(observed, ensure_ascii=False), encoding="utf-8")
    assert observed["inside_worktree"] == "true"
    assert observed["head_after"] == head_before
    assert observed["status_after"] == status_before
    return result
