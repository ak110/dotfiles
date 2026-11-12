# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのsync: remoteのテスト。"""

import os
import pathlib
import subprocess

import platformdirs
import pytest

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_sync_support import _remote_sync as _remote_sync


@pytest.mark.usefixtures("remote_sync")
class TestCommitAndPushRetry:
    """`commit_and_push`のpush失敗時再試行動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))

    def test_rebases_divergent_commits_and_pushes_both_results(self, tmp_path: pathlib.Path) -> None:
        """non-fast-forwardから復旧し、local/remoteの双方の本文をremoteへ送信する。"""
        remote, local, _other = TestExplicitUpstreamIntegration()._make_remote_and_clones(tmp_path)  # pylint: disable=protected-access
        (local / "local.md").write_text("ローカル成果\n", encoding="utf-8")
        with _wi_sync.repo_lock(local):
            _wi_sync.commit_and_push(local, "local result", ["local.md"])
        head = git_repository.git_output(local, "rev-parse", "HEAD")
        assert git_repository.git_output(remote, "rev-parse", "main") == head
        assert git_repository.git_output(local, "rev-parse", "@{u}") == head
        assert git_repository.git_output(remote, "show", "main:local.md") == "ローカル成果"
        assert git_repository.git_output(remote, "show", "main:queue.md") == "updated"
        assert git_repository.git_output(local, "status", "--porcelain") == ""

    def test_reraises_when_retry_push_also_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """再試行後もpushが失敗した場合は例外をそのまま送出する。"""

        attempts = []

        def fake_run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            del cwd, forward_error_output
            if args[0] == "push":
                attempts.append(args)
                raise subprocess.CalledProcessError(1, ["git", *args])
            if args[:2] == ["merge-base", "--is-ancestor"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", fake_run_git)
        monkeypatch.setattr(_atk_git_sync, "is_worktree_dirty", lambda _path, **_kwargs: False)

        with (
            pytest.raises(subprocess.CalledProcessError),
            _wi_sync.repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001

        assert len(attempts) == 2

    def test_keeps_real_rebase_conflict_and_reports_manual_steps(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """実rebaseの競合状態と双方のcommitを保持し、自動abortせず復旧を案内する。"""
        remote, local, _other = TestExplicitUpstreamIntegration()._make_remote_and_clones(tmp_path)  # pylint: disable=protected-access
        (local / "queue.md").write_text("ローカル競合\n", encoding="utf-8")
        before_remote = git_repository.git_output(remote, "rev-parse", "main")
        with pytest.raises(subprocess.CalledProcessError), _wi_sync.repo_lock(local):
            _wi_sync.commit_and_push(local, "local conflict", ["queue.md"])
        git_dir = local / ".git"
        assert (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir()
        assert git_repository.git_output(local, "show", "ORIG_HEAD:queue.md") == "ローカル競合"
        assert git_repository.git_output(remote, "rev-parse", "main") == before_remote
        assert git_repository.git_output(remote, "show", "main:queue.md") == "updated"
        assert "queue.md" in git_repository.git_output(local, "diff", "--name-only", "--diff-filter=U")
        assert "<<<<<<<" in (local / "queue.md").read_text(encoding="utf-8")
        error = capsys.readouterr().err
        assert "rebase状態を保持" in error
        assert "git add <競合解消済みパス>" in error
        assert "自動abortは行っていない" in error
        assert "git -c submodule.recurse=false rebase --abort" in error


class TestExplicitUpstreamIntegration:
    """実Gitで共有`FETCH_HEAD`とユーザー設定から独立した同期対象を検証する。"""

    def _make_remote_and_clones(
        self,
        tmp_path: pathlib.Path,
    ) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
        """mainとsideを持つbare remoteおよび同じmainを追跡する2作業コピーを作成する。"""
        remote = tmp_path / "remote.git"
        seed = tmp_path / "seed"
        old_copy = tmp_path / "old-copy"
        new_copy = tmp_path / "new-copy"
        git_repository.init_bare_repository(remote)
        git_repository.init_repository(seed, initial_branch="main")
        (seed / "queue.md").write_text("initial\n", encoding="utf-8")
        git_repository.run_git(seed, "add", "queue.md")
        git_repository.run_git(seed, "commit", "-m", "initial")
        git_repository.run_git(seed, "remote", "add", "origin", str(remote))
        git_repository.run_git(seed, "push", "-u", "origin", "main")
        git_repository.run_git(seed, "switch", "-c", "side")
        (seed / "side.md").write_text("side\n", encoding="utf-8")
        git_repository.run_git(seed, "add", "side.md")
        git_repository.run_git(seed, "commit", "-m", "side")
        git_repository.run_git(seed, "push", "-u", "origin", "side")
        git_repository.run_git(seed, "switch", "main")
        git_repository.run_git(tmp_path, "clone", str(remote), str(old_copy))
        git_repository.run_git(tmp_path, "clone", str(remote), str(new_copy))
        (seed / "queue.md").write_text("updated\n", encoding="utf-8")
        git_repository.run_git(seed, "add", "queue.md")
        git_repository.run_git(seed, "commit", "-m", "update")
        git_repository.run_git(seed, "push")
        return remote, old_copy, new_copy

    def test_explicit_upstream_succeeds_when_fetch_head_has_multiple_candidates(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """複数fetch候補ではpullによる再現は失敗し、明示upstream同期は成功する。"""
        _remote, old_copy, new_copy = self._make_remote_and_clones(tmp_path)

        old_result = git_repository.run_git(
            old_copy,
            "-c",
            "pull.rebase=true",
            "pull",
            "origin",
            "main",
            "side",
            check=False,
        )
        assert old_result.returncode != 0
        assert "multiple branches" in old_result.stderr

        original_run_git = _wi_sync.run_git  # pylint: disable=protected-access  # noqa: SLF001

        def run_with_competing_fetch(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            original_run_git(args, cwd, forward_error_output=forward_error_output)
            if args == ["fetch"]:
                git_repository.run_git(cwd, "fetch", "origin", "main", "side")

        monkeypatch.setattr(_wi_sync, "run_git", run_with_competing_fetch)
        with _wi_sync.repo_lock(new_copy):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull(new_copy)  # pylint: disable=protected-access  # noqa: SLF001

        assert (
            git_repository.run_git(new_copy, "rev-parse", "HEAD").stdout
            == git_repository.run_git(new_copy, "rev-parse", "@{u}").stdout
        )

    def test_sync_is_local_when_upstream_is_unset(self, tmp_path: pathlib.Path) -> None:
        """upstream未設定では別refを使わずローカルの状態を保って同期を省く。"""
        _remote, old_copy, _new_copy = self._make_remote_and_clones(tmp_path)
        git_repository.run_git(old_copy, "branch", "--unset-upstream")
        original = git_repository.git_output(old_copy, "rev-parse", "HEAD")
        with _wi_sync.repo_lock(old_copy):
            _wi_sync.pull(old_copy)  # pylint: disable=protected-access  # noqa: SLF001
        assert git_repository.git_output(old_copy, "rev-parse", "HEAD") == original

    def test_recent_predicate_requires_upstream_ancestor_in_addition_to_time(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """直近のFETCH_HEADだけでは再利用せず、upstreamがHEADの祖先の場合だけ再利用する。"""
        _remote, behind, ahead = self._make_remote_and_clones(tmp_path)
        git_repository.run_git(behind, "fetch")
        git_repository.run_git(ahead, "config", "user.email", "test@example.com")
        git_repository.run_git(ahead, "config", "user.name", "test")
        (ahead / "local.md").write_text("local\n", encoding="utf-8")
        git_repository.run_git(ahead, "add", "local.md")
        git_repository.run_git(ahead, "commit", "-m", "local")

        for repo in (behind, ahead):
            fetch_head = repo / ".git" / "FETCH_HEAD"
            fetch_head.touch()
            os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)

        assert _wi_sync.pulled_recently(behind) is False  # pylint: disable=protected-access  # noqa: SLF001
        assert _wi_sync.pulled_recently(ahead) is True  # pylint: disable=protected-access  # noqa: SLF001

    def test_recent_predicate_excludes_local_only_repository(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ローカル管理リポジトリはFETCH_HEADが直近でも再利用しない。"""
        local_only = tmp_path / "local-only"
        git_repository.init_repository(local_only, initial_branch="main")
        fetch_head = local_only / ".git" / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)

        assert _wi_sync.pulled_recently(local_only) is False  # pylint: disable=protected-access  # noqa: SLF001

    def test_recent_predicate_excludes_unresolvable_upstream(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """upstreamを解決できない場合はFETCH_HEADが直近でも再利用しない。"""
        repo = tmp_path / "untracked"
        git_repository.init_repository(repo, initial_branch="main", files={"entry.md": "entry\n"}, commit_message="initial")
        fetch_head = repo / ".git" / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)

        assert _wi_sync.pulled_recently(repo) is False  # pylint: disable=protected-access  # noqa: SLF001


class TestHasRemote:
    """upstreamの有無を使い、既存のマーカーに判定を左右されない。"""

    def test_legacy_marker_does_not_override_upstream(self, tmp_path: pathlib.Path) -> None:
        """マーカーが残る既存リポジトリも、upstreamの設定で同期対象になる。"""
        notes, remote = tmp_path / "notes", tmp_path / "remote.git"
        git_repository.init_repository(notes, initial_branch="main", files={"entry.md": "本文\n"}, commit_message="initial")
        marker = notes / ".agent-toolkit-local-only"
        marker.touch()
        assert not _wi_sync.has_remote(notes)
        git_repository.init_bare_repository(remote)
        git_repository.run_git(notes, "remote", "add", "origin", str(remote))
        git_repository.run_git(notes, "push", "-u", "origin", "main")
        assert _wi_sync.has_remote(notes) and marker.exists()


class TestPullAndCommitPushSkipWithoutRemote:
    """remote未設定のローカル限定リポジトリではpull・pushをスキップすることを検証する。"""

    def test_pull_is_noop_without_remote(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """upstreamの無いディレクトリでは`pull`がremote同期を実行しない。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert not any(call[0] in ("fetch", "merge") for call in calls)

    def test_commit_and_push_skips_push_without_remote(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """upstreamの無いディレクトリでは`commit_and_push`がadd・commitのみ実行しpushしない。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001
        assert calls == [["add", "--all", "--", "inbox"], ["commit", "-m", "chore: test", "--", "inbox"]]


@pytest.mark.usefixtures("remote_sync")
class TestPullIfStale:
    """定期バックグラウンド更新のレート制限を検証する。"""

    def test_skips_recent_pull(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """直近pull後の定期更新を省略することを確認する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            assert _wi_sync.pull_if_stale(tmp_path) is False
        assert not any(call[0] in ("fetch", "merge") for call in calls)

    def test_pulls_when_due(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """更新期限を過ぎた定期更新がpullすることを確認する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1100.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            assert _wi_sync.pull_if_stale(tmp_path) is True
        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]

    def test_pulls_when_fetch_head_missing(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`FETCH_HEAD`が無い場合は経過時間を判定できないためpullする。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            assert _wi_sync.pull_if_stale(tmp_path) is True
        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]

    def test_public_pull_ignores_rate_limit(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ユーザーの操作に対応する`pull`は直近pullの有無によらず毎回実行する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull(tmp_path)
        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]


@pytest.mark.usefixtures("remote_sync")
class TestPullWithRecentNotice:
    """読み取り専用操作の同期再利用・強制同期・移行契約を検証する。"""

    @pytest.mark.parametrize("mode", ["recent", "forced", "expired"])
    def test_recent_and_forced_reads_preserve_or_refresh_saved_entries(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        mode: str,
    ) -> None:
        """直近同期は保存済みsnapshotを使い、強制指定と期限後はremoteの新しい本文を取り込む。"""
        remote, local, peer = TestExplicitUpstreamIntegration()._make_remote_and_clones(tmp_path)  # pylint: disable=protected-access
        with _wi_sync.repo_lock(local):
            _wi_sync.pull(local)
        before = git_repository.git_output(local, "rev-parse", "HEAD")
        fetch_mtime = (local / ".git/FETCH_HEAD").stat().st_mtime
        git_repository.run_git(peer, "pull", "--ff-only")
        (peer / "new.md").write_text("新しい本文\n", encoding="utf-8")
        latest = git_repository.commit_all(peer, "new remote body")
        git_repository.run_git(peer, "push")
        monkeypatch.setattr(_wi_sync.time, "time", lambda: fetch_mtime + (30.0 if mode == "expired" else 10.0))
        with _wi_sync.repo_lock(local):
            _wi_sync.pull_with_recent_reuse(local, force_pull=mode == "forced")
        expected = before if mode == "recent" else latest
        assert git_repository.git_output(local, "rev-parse", "HEAD") == expected
        assert git_repository.git_output(remote, "rev-parse", "main") == latest
        assert (local / "queue.md").read_text(encoding="utf-8") == "updated\n"
        assert git_repository.git_output(local, "status", "--porcelain") == ""
        if mode == "recent":
            assert not (local / "new.md").exists()
        else:
            assert (local / "new.md").read_text(encoding="utf-8") == "新しい本文\n"

    def test_recent_reuse_still_migrates_legacy_reservations(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """remote同期を再利用しても旧予約移行は実行する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        migrations: list[pathlib.Path] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005

        def migrate(private_notes: pathlib.Path) -> int:
            migrations.append(private_notes)
            return 0

        monkeypatch.setattr(_wi_sync, "migrate_legacy_reservations", migrate)
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull_with_recent_reuse(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert [call for call in calls if call[0] in ("fetch", "merge")] == []
        assert migrations == [tmp_path]

    def test_fetch_failure_stops_before_merge_and_migration(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetch失敗時はmergeと旧予約移行へ進まず例外を送出する。"""
        calls: list[list[str]] = []
        migrations: list[pathlib.Path] = []

        def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            del forward_error_output
            del cwd
            calls.append(args)
            if args == ["fetch"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", run_git)

        def migrate(private_notes: pathlib.Path) -> int:
            migrations.append(private_notes)
            return 0

        monkeypatch.setattr(_wi_sync, "migrate_legacy_reservations", migrate)
        with (
            pytest.raises(subprocess.CalledProcessError),
            _wi_sync.repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _wi_sync.pull_with_recent_reuse(tmp_path, force_pull=True)  # pylint: disable=protected-access  # noqa: SLF001

        assert not any(call[0] in ("merge", "rebase", "commit", "push") for call in calls)
        assert not migrations

    def test_merge_failure_stops_before_legacy_migration(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """merge失敗時は旧予約移行へ進まず例外を送出する。"""
        calls: list[list[str]] = []
        migrations: list[pathlib.Path] = []

        def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            del forward_error_output
            del cwd
            calls.append(args)
            if args == ["merge", "--ff-only", "@{u}"]:
                raise subprocess.CalledProcessError(128, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", run_git)

        def migrate(private_notes: pathlib.Path) -> int:
            migrations.append(private_notes)
            return 0

        monkeypatch.setattr(_wi_sync, "migrate_legacy_reservations", migrate)
        with (
            pytest.raises(subprocess.CalledProcessError),
            _wi_sync.repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _wi_sync.pull_with_recent_reuse(tmp_path, force_pull=True)  # pylint: disable=protected-access  # noqa: SLF001

        assert not any(call[0] in ("rebase", "commit", "push") for call in calls)
        assert not migrations

    def test_failed_merge_after_recent_fetch_is_retried_and_rebased(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fetch後のff-only統合失敗直後は、直近mtimeでも再試行して分岐をrebaseする。"""
        integration = TestExplicitUpstreamIntegration()
        _remote, local, _other = integration._make_remote_and_clones(tmp_path)  # pylint: disable=protected-access
        git_repository.run_git(local, "config", "user.email", "test@example.com")
        git_repository.run_git(local, "config", "user.name", "test")
        (local / "local.md").write_text("local\n", encoding="utf-8")
        git_repository.run_git(local, "add", "local.md")
        git_repository.run_git(local, "commit", "-m", "local")
        git_repository.run_git(local, "fetch")
        merge_result = git_repository.run_git(local, "merge", "--ff-only", "@{u}", check=False)
        assert merge_result.returncode != 0
        fetch_head = local / ".git" / "FETCH_HEAD"
        fetch_mtime = fetch_head.stat().st_mtime
        monkeypatch.setattr(_wi_sync.time, "time", lambda: fetch_mtime + 1.0)

        migrations: list[pathlib.Path] = []

        def migrate(private_notes: pathlib.Path) -> int:
            migrations.append(private_notes)
            return 0

        monkeypatch.setattr(_wi_sync, "migrate_legacy_reservations", migrate)
        with _wi_sync.repo_lock(local):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull_with_recent_reuse(local)  # pylint: disable=protected-access  # noqa: SLF001

        assert git_repository.run_git(local, "merge-base", "--is-ancestor", "@{u}", "HEAD", check=False).returncode == 0
        assert (local / "local.md").read_text(encoding="utf-8") == "local\n"
        assert (local / "queue.md").read_text(encoding="utf-8") == "updated\n"
        assert git_repository.git_output(local, "status", "--porcelain") == ""
        assert migrations == [local]
