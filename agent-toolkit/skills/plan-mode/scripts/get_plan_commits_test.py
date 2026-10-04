"""計画で確定した公開名から同じ実装commit対応を取得する。"""

import argparse
import json
import pathlib
import subprocess

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan import commit_mapping


def test_public_plan_commits_reads_existing_handoff(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """記録済みの対応を公開登録から取得し、実在する完全OIDを返す。"""
    for args in [
        ["init"],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "test"],
    ]:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, timeout=30)
    wi = "20261004-044311-001.md"
    event = commit_mapping.commit_event(tmp_path, "HEAD", [wi], {wi})
    record = tmp_path / "handoff.md"
    record.write_text(commit_mapping.encode_event(event), encoding="utf-8")
    args = argparse.Namespace(
        script_name="plan-commits",
        script_args=[str(record), "--worktree", str(tmp_path), "--awi", wi, "--handoff", "--allowed-awi", wi],
    )
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out) == {"awi": wi, "commits": [event["commit"]]}
