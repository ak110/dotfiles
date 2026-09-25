"""`_git_status`モジュールのテスト。"""

import pathlib
import subprocess

import pytest

from agent_toolkit._git import status as _git_status


def _init_git_repo(path: pathlib.Path) -> None:
    """最小git repo初期化。"""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True)


def _git_commit_initial(path: pathlib.Path, files: dict[str, str]) -> None:
    """指定ファイルを追加してinitial commitを作成する。"""
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


class TestRunGitLines:
    """`run_git_lines`: git出力を行リストで返す。"""

    def test_successful_command_returns_lines(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        result = _git_status.run_git_lines(["git", "remote"], str(repo))
        assert result == []  # リモート未構成

    def test_failed_command_returns_none(self, tmp_path: pathlib.Path):
        result = _git_status.run_git_lines(["git", "config", "nonexistent"], str(tmp_path / "nonexistent"))
        assert result is None

    def test_timeout_returns_none(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        def _raise_timeout(*_args: object, **_kwargs: object) -> None:
            raise subprocess.TimeoutExpired(cmd="git", timeout=10)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        result = _git_status.run_git_lines(["git", "remote"], str(tmp_path))
        assert result is None

    def test_os_error_returns_none(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        def _raise_os_error(*_args: object, **_kwargs: object) -> None:
            raise OSError("simulated failure")

        monkeypatch.setattr(subprocess, "run", _raise_os_error)
        result = _git_status.run_git_lines(["git", "remote"], str(tmp_path))
        assert result is None

    def test_blank_lines_are_filtered(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        completed = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="a\n\n  \nb\n")

        def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return completed

        monkeypatch.setattr(subprocess, "run", _fake_run)
        result = _git_status.run_git_lines(["git", "remote"], str(tmp_path))
        assert result == ["a", "b"]
