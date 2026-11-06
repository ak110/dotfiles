"""別プロセスの公開レーン統合・受理で、環境差による検証の再利用を確認する。"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._atk.lane_test import prepare_lane_verification
from agent_toolkit._common import inherited_venv
from agent_toolkit._testing import git_repository


@pytest.mark.parametrize("difference", ["normalized-launch", "unknown-input"])
def test_public_acceptance_in_another_process_preserves_reuse_boundaries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], difference: str
) -> None:
    """受理前に保存した同じ成功を使い、未知の入力差だけが検証を再起動する。"""
    marker = tmp_path / "executions"
    code = (
        "import pathlib,sys; path=pathlib.Path(sys.argv[1]); "
        "path.write_text(str(int(path.read_text())+1) if path.exists() else '1'); print('verified')"
    )
    arguments = prepare_lane_verification(tmp_path / "case", monkeypatch, capsys, [sys.executable, "-c", code, str(marker)])
    record_path = pathlib.Path(arguments[arguments.index("--record") + 1])
    record = json.loads(record_path.read_text(encoding="utf-8"))
    worktree = pathlib.Path(record["worktree"])
    target = pathlib.Path(arguments[arguments.index("--target-worktree") + 1])
    (worktree / "feature.txt").write_text("lane\n", encoding="utf-8")
    reviewed = git_repository.commit_all(worktree, "成果")
    (target / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    git_repository.commit_all(target, "先行統合")
    arguments[arguments.index("--reviewed-head") + 1] = reviewed
    first = subprocess.run(
        arguments, env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", timeout=45, check=False
    )
    assert first.returncode == 1, first.stderr
    pending = json.loads(first.stdout)
    assert pending["phase"] == "awaiting-validation" and marker.read_text(encoding="utf-8") == "1"
    environment = dict(os.environ)
    environment["LANE_VERIFICATION_INPUT"] = "new-private-input"
    if difference == "normalized-launch":
        environment[inherited_venv.LAUNCH_RESTORE_KEY] = json.dumps({"LANE_VERIFICATION_INPUT": [None, "new-private-input"]})
    accepted = subprocess.run(
        [*arguments, "--accept-rebased-head", pending["new_head"]],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr
    result = json.loads(accepted.stdout)
    assert result["phase"] == "finalized"
    assert marker.read_text(encoding="utf-8") == ("1" if difference == "normalized-launch" else "2")
    verification = result["results"]["verification-0"]
    assert ("reused_from" in verification) == (difference == "normalized-launch")
    assert "new-private-input" not in accepted.stderr
    if difference == "unknown-input":
        assert "LANE_VERIFICATION_INPUT" in accepted.stderr
