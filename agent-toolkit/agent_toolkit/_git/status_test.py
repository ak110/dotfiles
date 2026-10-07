"""`_git_status`モジュールのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit._git import status as _git_status
from agent_toolkit._testing import git_repository


class TestRunGitLines:
    """`run_git_lines`: git出力を行リストで返す。"""

    def test_successful_command_returns_lines(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo"
        git_repository.init_repository(repo, files={"a.txt": "content"}, commit_message="init")
        result = _git_status.run_git_lines(["remote"], str(repo))
        assert result == []  # リモート未構成

    def test_failed_command_returns_none(self, tmp_path: pathlib.Path):
        result = _git_status.run_git_lines(["config", "nonexistent"], str(tmp_path / "nonexistent"))
        assert result is None

    def test_timeout_returns_none(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        def _raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        result = _git_status.run_git_lines(["remote"], str(tmp_path))
        assert result is None

    def test_os_error_returns_none(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        def _raise_os_error(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated failure")

        monkeypatch.setattr(subprocess, "run", _raise_os_error)
        result = _git_status.run_git_lines(["remote"], str(tmp_path))
        assert result is None

    def test_blank_lines_are_filtered(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        completed = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="a\n\n  \nb\n")

        def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return completed

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = _git_status.run_git_lines(["remote"], str(tmp_path))
        assert result == ["a", "b"]
