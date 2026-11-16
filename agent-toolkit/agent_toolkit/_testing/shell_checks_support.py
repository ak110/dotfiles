"""shell_checks_testの共通試験準備。"""

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
    run_without_read_delivery,
)


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """シェル固有の無通知契約を、作業契約を作成済みの状態で検証する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: シェル操作の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


def _run_in_ready_context(payload: dict, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """対象外の資料取得を代用し、シェル・MCP固有の公開判定を試す。"""
    return run_without_read_delivery(payload, env_overrides=env_overrides)


def _denial_reason(result: subprocess.CompletedProcess[str]) -> str:
    """正常拒否の終了状態と配送先を確かめ、理由を返す。"""
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
    """実Gitを持つ場所から公開hookへ渡し、判定だけで履歴や作業状態を変えないことを確認する。"""
    head_before = git_repository.git_output(repo, "rev-parse", "HEAD")
    status_before = git_repository.git_output(repo, "status", "--porcelain=v1")
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo), **extra_payload}
    env = _plan_file_state_env(tmp_path)
    # 本試験はcommit本文の検査を対象とする。repository保護を通す登録を明示する。
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
