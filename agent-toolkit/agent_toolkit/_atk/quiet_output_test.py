"""明示的な正常選択と対応を要する出力を区別する。"""

import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import git_sync


@pytest.mark.parametrize("paths", [[], ["target.txt"]])
def test_skip_push_keeps_commit_without_known_notice(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    paths: list[str],
) -> None:
    calls: list[list[str]] = []

    def run_git(args: list[str], cwd: pathlib.Path, **_kwargs: Any) -> None:
        del cwd
        calls.append(args)

    monkeypatch.setattr(git_sync, "assert_repo_lock_held", lambda *_args, **_kwargs: None)
    result = git_sync.commit_and_push(tmp_path, "対象を保存", paths, skip_push=True, run_git=run_git)

    assert result == 0
    expected = [["add", "--all", "--", "target.txt"], ["commit", "-m", "対象を保存", "--", "target.txt"]] if paths else []
    assert calls == expected
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


def test_push_failure_keeps_reason_and_recovery(capsys: pytest.CaptureFixture[str]) -> None:
    failure = subprocess.CalledProcessError(128, ["git", "push"], stderr="Authentication failed")
    git_sync._report_sync_failure(pathlib.Path("notes"), "push", failure)  # pylint: disable=protected-access

    output = capsys.readouterr().err
    assert "失敗:" in output
    assert "認証に失敗" in output
    assert "次の操作:" in output
    assert "remote -v" in output
