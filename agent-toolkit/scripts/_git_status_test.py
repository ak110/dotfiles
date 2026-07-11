"""`_git_status`モジュールのテスト。"""

import pathlib
import subprocess

import _git_status


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


class TestIsTrackedChange:
    """`is_tracked_change`: untracked行（`??`）除外判定。"""

    def test_tracked_modification_line_is_tracked(self):
        assert _git_status.is_tracked_change(" M a.txt") is True

    def test_untracked_line_is_not_tracked(self):
        assert _git_status.is_tracked_change("?? new.txt") is False

    def test_empty_line_is_not_tracked(self):
        assert _git_status.is_tracked_change("") is False


class TestHasTrackedDirty:
    """`has_tracked_dirty`: 追跡ファイルの未コミット差分有無判定。"""

    def test_empty_cwd_returns_none(self):
        assert _git_status.has_tracked_dirty("") is None

    def test_non_git_dir_returns_none(self, tmp_path: pathlib.Path):
        assert _git_status.has_tracked_dirty(str(tmp_path)) is None

    def test_clean_repo_returns_false(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo-clean"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        assert _git_status.has_tracked_dirty(str(repo)) is False

    def test_tracked_modification_returns_true(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo-dirty"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "a.txt").write_text("modified", encoding="utf-8")
        assert _git_status.has_tracked_dirty(str(repo)) is True

    def test_untracked_only_returns_false(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo-untracked"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "initial"})
        (repo / "new.txt").write_text("new", encoding="utf-8")
        assert _git_status.has_tracked_dirty(str(repo)) is False


class TestGitPushIsRealSend:
    """`git_push_is_real_send`: `--dry-run`/`-n`未指定の実送出push判定。"""

    def test_no_flags_is_real_send(self):
        assert _git_status.git_push_is_real_send(["origin", "main"]) is True

    def test_dry_run_long_flag_is_not_real_send(self):
        assert _git_status.git_push_is_real_send(["--dry-run", "origin", "main"]) is False

    def test_dry_run_short_flag_is_not_real_send(self):
        assert _git_status.git_push_is_real_send(["-n", "origin", "main"]) is False
