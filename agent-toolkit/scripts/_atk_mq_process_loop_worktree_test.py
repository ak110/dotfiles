"""`atk mq process-loop`のworktree追随処理のテスト。

本体テストの`_atk_mq_process_loop_test.py`が行数上限に達したため、
worktree追随という責務の境界で分割した。
"""

import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position


class TestSyncWorktreeWithUpstream:
    """反復間で再利用するworktreeを上流最新へ追随させる処理。"""

    @staticmethod
    def _make_worktree(tmp_path: pathlib.Path) -> pathlib.Path:
        """対象リポジトリ配下へworktreeディレクトリを生成し、そのパスを返す。"""
        worktree = tmp_path / "repo" / ".claude" / "worktrees" / "process-loop"
        worktree.mkdir(parents=True)
        return worktree

    def test_rebases_existing_worktree_onto_upstream(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """既存worktreeに対しfetchと上流ブランチへのrebaseを実行する。"""
        worktree = self._make_worktree(tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            stdout = "origin/master" if cmd[1:2] == ["symbolic-ref"] else ""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _process_loop._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "fetch", "origin"] in calls
        assert ["git", "rebase", "origin/master"] in calls
        assert worktree.is_dir()

    def test_reuses_existing_branch_when_worktree_absent(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """worktree未作成でも専用ブランチがあれば同ブランチから作成する。"""
        local_path = tmp_path / "repo"
        worktree = local_path / ".claude" / "worktrees" / "process-loop"
        calls: list[list[str]] = []
        monkeypatch.setattr(_process_loop, "_git_output", lambda *_args, **_kwargs: "origin/master")
        monkeypatch.setattr(_process_loop, "_worktree_is_clean", lambda path: path == worktree)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _process_loop._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "worktree", "add", str(worktree), "worktree-process-loop"] in calls

    def test_aborts_rebase_and_warns_on_conflict(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rebase失敗時は中断したうえで警告を発し、writerを起動しない。"""
        self._make_worktree(tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:2] == ["symbolic-ref"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="origin/master", stderr="")
            if cmd[1:3] == ["rebase", "origin/master"]:
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="conflict")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _process_loop._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "rebase", "--abort"] in calls
        assert "追随に失敗したためwriterを起動しません" in capsys.readouterr().err
