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
            if cmd[1:3] == ["worktree", "add"]:
                worktree.mkdir(parents=True)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _process_loop._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "worktree", "add", str(worktree), "worktree-process-loop"] in calls
        assert ["git", "rebase", "origin/master"] in calls

    def test_recreated_worktree_rebases_existing_branch_onto_upstream(self, tmp_path: pathlib.Path) -> None:
        """遅れた専用ブランチのworktreeを再作成して上流へ追随させる。"""
        origin = tmp_path / "origin.git"
        seed = tmp_path / "seed"
        local_path = tmp_path / "repo"

        def run_git(args: list[str], cwd: pathlib.Path = tmp_path) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

        run_git(["init", "--bare", "--initial-branch=master", str(origin)])
        run_git(["init", "--initial-branch=master", str(seed)])
        run_git(["config", "user.name", "test"], cwd=seed)
        run_git(["config", "user.email", "test@example.com"], cwd=seed)
        (seed / "state.txt").write_text("base\n", encoding="utf-8")
        run_git(["add", "state.txt"], cwd=seed)
        run_git(["commit", "-m", "base"], cwd=seed)
        run_git(["remote", "add", "origin", str(origin)], cwd=seed)
        run_git(["push", "-u", "origin", "master"], cwd=seed)
        run_git(["clone", str(origin), str(local_path)])
        run_git(["branch", "worktree-process-loop"], cwd=local_path)

        (seed / "state.txt").write_text("upstream\n", encoding="utf-8")
        run_git(["commit", "-am", "upstream"], cwd=seed)
        run_git(["push", "origin", "master"], cwd=seed)

        worktree = local_path / ".claude" / "worktrees" / "process-loop"
        result = _process_loop._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", "origin/master", "HEAD"], cwd=worktree, check=False)
        assert ancestry.returncode == 0

    def test_aborts_rebase_and_warns_on_conflict(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rebase失敗時は中断したうえで警告を発し、実装セッションを起動しない。"""
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
        assert "追随に失敗したため実装セッションを起動しません" in capsys.readouterr().err
