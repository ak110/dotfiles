"""`_git_status`モジュールのテスト。"""

import pathlib
import subprocess

import _git_status
import pytest


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


class TestListRemotes:
    """`list_remotes`: 構成済みリモート名一覧を返す。"""

    def test_no_remotes_returns_empty_list(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        result = _git_status.list_remotes(str(repo))
        assert result == []

    def test_nonexistent_repo_returns_empty_list(self, tmp_path: pathlib.Path):
        result = _git_status.list_remotes(str(tmp_path / "nonexistent"))
        assert result == []

    def test_multiple_remotes_returns_all_names(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "/tmp/does-not-exist-origin"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "upstream", "/tmp/does-not-exist-upstream"], check=True)
        result = _git_status.list_remotes(str(repo))
        assert sorted(result) == ["origin", "upstream"]


class TestSnapshotRemoteRefs:
    """`snapshot_remote_refs`: リモート参照のスナップショットを返す。

    戻り値`dict[str, dict[str, str] | None]`のうち、値`None`は「`git ls-remote`が失敗した
    既知のリモート」を示すマーカーである（リモート名自体はキーとして保持する）。
    """

    def test_no_remotes_returns_empty_dict(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        result = _git_status.snapshot_remote_refs(str(repo))
        assert not result

    def test_failed_ls_remote_marks_remote_as_none(self, tmp_path: pathlib.Path):
        """`git ls-remote`が失敗するリモート（存在しないローカルパス指定）は値を`None`とし、
        キー自体（リモート名の既知性）は保持する。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", str(tmp_path / "no-such-remote-path")],
            check=True,
        )
        result = _git_status.snapshot_remote_refs(str(repo))
        assert result == {"origin": None}

    def test_reachable_remote_returns_ref_dict(self, tmp_path: pathlib.Path):
        """到達可能なリモート（ローカルbareリポジトリ）のref名・OIDを辞書化する。"""
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
        result = _git_status.snapshot_remote_refs(str(repo))
        assert "origin" in result
        refs = result["origin"]
        assert isinstance(refs, dict)
        assert "refs/heads/main" in refs
        expected_oid = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "main"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert refs["refs/heads/main"] == expected_oid

    def test_mixed_reachable_and_failed_remotes(self, tmp_path: pathlib.Path):
        """複数リモートが混在する場合、到達可能なリモートはref辞書、失敗リモートは`None`となる。"""
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "broken", str(tmp_path / "no-such-remote-path")],
            check=True,
        )
        result = _git_status.snapshot_remote_refs(str(repo))
        assert result["broken"] is None
        assert isinstance(result["origin"], dict)
        assert "refs/heads/main" in result["origin"]


class TestResolveDefaultBranch:
    """`resolve_default_branch`: 構成済みリモートのHEAD参照から既定ブランチ名を解決する。"""

    def test_no_remotes_returns_none(self, tmp_path: pathlib.Path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        assert _git_status.resolve_default_branch(str(repo)) is None

    def test_single_remote_with_head_returns_branch(self, tmp_path: pathlib.Path):
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "set-head", "origin", "main"], check=True)
        assert _git_status.resolve_default_branch(str(repo)) == "origin/main"

    def test_first_remote_unresolvable_falls_through_to_second(self, tmp_path: pathlib.Path):
        """先頭リモートのHEAD解決に失敗しても、後続リモートの解決を試みる。"""
        bare = tmp_path / "remote.git"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        _git_commit_initial(repo, {"a.txt": "content"})
        subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "aaa", str(tmp_path / "no-such-remote-path")],
            check=True,
        )
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "set-head", "origin", "main"], check=True)
        assert _git_status.resolve_default_branch(str(repo)) == "origin/main"
