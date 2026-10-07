"""WI保存リポジトリの準備、remote同期、排他ロックとcommit・pushのテスト。"""

import os
import pathlib
import subprocess
import threading
import time

import filelock
import platformdirs
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common import private_notes as _private_notes
from agent_toolkit._testing import git_repository


def test_run_git_suppresses_success_output(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """WI共通処理のGit実行は成功時に標準出力と標準エラーへ書かない。"""
    _wi_sync.run_git(["init", "--initial-branch=main"], tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


class TestRepoLock:
    """`repo_lock`のプロセス間排他動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))

    def test_second_acquire_times_out_while_held(self, tmp_path: pathlib.Path) -> None:
        """1つ目のロック保持中は、別インスタンスからの2つ目の取得がタイムアウトする。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        lock1 = _wi_sync.repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        lock1.acquire()
        try:
            lock2 = _wi_sync.repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
            with pytest.raises(filelock.Timeout):
                lock2.acquire(timeout=0.2)
        finally:
            lock1.release()

    def test_constructor_timeout_bounds_plain_with_statement(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock(..., timeout=...)`のコンストラクタで設定した値が`with lock:`（引数無し取得）へ伝搬する。

        Web要求を処理する際は`acquire(timeout=...)`を明示呼び出しせず`with _repo_lock(private_notes, timeout=...):`
        の形でロックを使うため、コンストラクタで指定した`timeout`が実際の`with`文へ反映されることを保証する。
        """
        target = tmp_path / "private-notes"
        target.mkdir()
        lock1 = _wi_sync.repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        lock1.acquire()
        try:
            with (
                pytest.raises(filelock.Timeout),
                _wi_sync.repo_lock(target, timeout=0.2),  # pylint: disable=protected-access  # noqa: SLF001
            ):
                pass
        finally:
            lock1.release()

    def test_second_acquire_succeeds_after_release(self, tmp_path: pathlib.Path) -> None:
        """1つ目のロック解放後は、別インスタンスからの2つ目の取得が成功する。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        with _wi_sync.repo_lock(target):  # pylint: disable=protected-access  # noqa: SLF001
            pass
        lock2 = _wi_sync.repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        with lock2:
            assert lock2.is_locked

    def test_concurrent_transactions_are_serialized(self, tmp_path: pathlib.Path) -> None:
        """2スレッドが同時に`repo_lock`を取得しても、臨界区間が直列化されること。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        order: list[str] = []

        def worker(label: str) -> None:
            with _wi_sync.repo_lock(target):  # pylint: disable=protected-access  # noqa: SLF001
                order.append(f"{label}-start")
                time.sleep(0.05)
                order.append(f"{label}-end")

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        time.sleep(0.01)
        t2.start()
        t1.join()
        t2.join()

        assert order in (
            ["a-start", "a-end", "b-start", "b-end"],
            ["b-start", "b-end", "a-start", "a-end"],
        )


class TestAssertRepoLockHeld:
    """`assert_repo_lock_held`の不変条件表明を検証する。"""

    def test_pull_raises_runtime_error_when_lock_not_held(self, tmp_path: pathlib.Path) -> None:
        """`repo_lock`未保持で`pull`を呼ぶと`RuntimeError`を送出する。"""
        with pytest.raises(RuntimeError, match="不変条件違反"):
            _wi_sync.pull(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    def test_commit_and_push_raises_runtime_error_when_lock_not_held(self, tmp_path: pathlib.Path) -> None:
        """`repo_lock`未保持で`commit_and_push`を呼ぶと`RuntimeError`を送出する。"""
        with pytest.raises(RuntimeError, match="不変条件違反"):
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001


class TestCommitAndPushRetry:
    """`commit_and_push`のpush失敗時再試行動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))

    def test_retries_once_after_explicit_upstream_rebase_on_push_failure(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """履歴分岐のpush失敗時はfetch後に明示したupstreamへrebaseし、pushを1回だけ再試行する。"""
        calls: list[list[str]] = []
        push_attempts = 0

        def fake_run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            nonlocal push_attempts
            del cwd, forward_error_output
            calls.append(args)
            if args[0] == "push":
                push_attempts += 1
                if push_attempts == 1:
                    raise subprocess.CalledProcessError(1, ["git", *args])
            if args[:2] == ["merge-base", "--is-ancestor"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", fake_run_git)
        monkeypatch.setattr(_atk_git_sync, "is_worktree_dirty", lambda _path, **_kwargs: False)  # pylint: disable=protected-access  # noqa: SLF001

        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [
            ["add", "--all", "--", "inbox"],
            ["commit", "-m", "chore: test", "--", "inbox"],
            ["push"],
            ["fetch"],
            ["merge-base", "--is-ancestor", "HEAD", "@{u}"],
            ["merge-base", "--is-ancestor", "@{u}", "HEAD"],
            ["rebase", "@{u}"],
            ["push"],
        ]

    def test_reraises_when_retry_push_also_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """再試行後もpushが失敗した場合は例外をそのまま送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            del cwd, forward_error_output
            if args[0] == "push":
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", fake_run_git)

        with (
            pytest.raises(subprocess.CalledProcessError),
            _wi_sync.repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001

    def test_keeps_rebase_state_and_reports_manual_steps_when_rebase_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rebaseが失敗した場合はabortせず、状態と手動手順をstderrへ出力する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            del cwd, forward_error_output
            if args[0] == "push" or args == ["rebase", "@{u}"]:
                raise subprocess.CalledProcessError(1, ["git", *args])
            if args[:2] == ["merge-base", "--is-ancestor"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", fake_run_git)
        monkeypatch.setattr(_atk_git_sync, "is_worktree_dirty", lambda _path, **_kwargs: False)  # pylint: disable=protected-access  # noqa: SLF001

        with pytest.raises(subprocess.CalledProcessError), _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001

        error = capsys.readouterr().err
        assert "rebase状態を保持" in error
        assert "git add <競合解消済みパス>" in error
        assert "自動abortは行っていない" in error
        assert "git rebase --abort" in error

    def test_reports_conflict_path_when_rebase_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rebase失敗時に競合解消手順をstderrへ出力してから例外を送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            del cwd, forward_error_output
            if args[0] == "push" or args == ["rebase", "@{u}"]:
                raise subprocess.CalledProcessError(1, ["git", *args])
            if args[:2] == ["merge-base", "--is-ancestor"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_wi_sync, "run_git", fake_run_git)
        monkeypatch.setattr(_atk_git_sync, "is_worktree_dirty", lambda _path, **_kwargs: False)  # pylint: disable=protected-access  # noqa: SLF001

        with (
            pytest.raises(subprocess.CalledProcessError),
            _wi_sync.repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001

        assert "rebase状態を保持" in capsys.readouterr().err


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

    def test_sync_fails_when_upstream_is_unset(self, tmp_path: pathlib.Path) -> None:
        """upstream未設定では暗黙の別refへ退避せず同期を失敗させる。"""
        _remote, old_copy, _new_copy = self._make_remote_and_clones(tmp_path)
        git_repository.run_git(old_copy, "branch", "--unset-upstream")

        with pytest.raises(subprocess.CalledProcessError), _wi_sync.repo_lock(old_copy):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull(old_copy)  # pylint: disable=protected-access  # noqa: SLF001

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
        (local_only / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
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


class TestPrivateNotesAutoCreate:
    """`AGENT_TOOLKIT_PRIVATE_NOTES`未設定かつ省略時に使うパスが不在の場合のローカルリポジトリ自動生成を検証する。

    conftestが適用する隔離（`agent_toolkit._testing.isolation`）が全テストへ環境変数を設定するため、
    本クラスの各テストは`monkeypatch.delenv`で明示的に解除してから検証する。
    """

    @pytest.fixture(autouse=True)
    def _isolate_data_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """自動生成先を実環境の`user_data_dir`から隔離し、テストの準備で設定した環境変数による上書きを解除する。"""
        monkeypatch.delenv("AGENT_TOOLKIT_PRIVATE_NOTES", raising=False)
        monkeypatch.setattr(platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))

    def test_private_notes_path_falls_back_to_platformdirs_when_default_missing(self, tmp_path: pathlib.Path) -> None:
        """未指定の場合に使う`home/private-notes`が不在の場合、platformdirs配下へフォールバックする。"""
        home = tmp_path / "home"
        home.mkdir()
        resolved = _private_notes.default_private_notes(home)
        assert resolved == tmp_path / "data" / "private-notes"

    def test_private_notes_path_prefers_existing_default(self, tmp_path: pathlib.Path) -> None:
        """省略時に使うパスが実在する場合はplatformdirsへフォールバックせずそちらを返す。"""
        home = tmp_path / "home"
        (home / "private-notes").mkdir(parents=True)
        resolved = _private_notes.default_private_notes(home)
        assert resolved == home / "private-notes"

    def test_ensure_environment_initializes_local_repo(self, tmp_path: pathlib.Path) -> None:
        """省略時に使うパスが不在の場合、`ensure_environment`はローカルgitリポジトリを自動生成して返す。"""
        home = tmp_path / "home"
        home.mkdir()
        root = _wi_sync.ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert root == tmp_path / "data" / "private-notes"
        assert (root / ".git").is_dir()
        assert (root / _wi_sync.LOCAL_ONLY_MARKER).exists()  # pylint: disable=protected-access  # noqa: SLF001
        expected_state_dirs = (
            "inbox",
            "processing",
            "adopted",
            "rejected",
            "inbox",
            "adopted",
        )
        for name in expected_state_dirs:
            assert (root / name).is_dir()
        exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert exclude.splitlines().count(_file_lock.PLAN_LOCK_IGNORE_PATTERN) == 1
        assert not (root / ".gitignore").exists()
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_wi_list_initializes_local_repo_without_git_output(
        self,
        tmp_path: pathlib.Path,
        capfd: pytest.CaptureFixture[str],
    ) -> None:
        """初回の`wi list`はローカルリポジトリを生成してもGitの成功出力を残さない。"""
        home = tmp_path / "home"
        home.mkdir()

        with pytest.raises(SystemExit) as excinfo:
            atk.main(["wi", "list"], home=home)

        captured = capfd.readouterr()
        assert excinfo.value.code == 0
        assert captured.out == ""
        assert captured.err == ""

    def test_ensure_environment_is_idempotent(self, tmp_path: pathlib.Path) -> None:
        """2回連続で呼んでも2回目は既存のローカルリポジトリをそのまま返す（再初期化しない）。"""
        home = tmp_path / "home"
        home.mkdir()
        first = _wi_sync.ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        marker = first / "sentinel.txt"
        marker.write_text("kept", encoding="utf-8")
        second = _wi_sync.ensure_environment(home)  # pylint: disable=protected-access  # noqa: SLF001
        assert second == first
        assert marker.read_text(encoding="utf-8") == "kept"

    def test_ensure_environment_excludes_plan_lock_without_commit(self, tmp_path: pathlib.Path) -> None:
        """計画ロックの除外を版管理の対象外へ記録し、commitも作業ツリーの差分も生じない。"""
        home = tmp_path / "home"
        root = home / "private-notes"
        git_repository.init_repository(root)
        (root / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        (root / "README.md").write_text("base\n", encoding="utf-8")
        git_repository.commit_all(root, "base")
        original_head = git_repository.git_output(root, "rev-parse", "HEAD")
        lock = root / "plans" / ".agent-toolkit-plan-create.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()

        assert _wi_sync.ensure_environment(home) == root  # pylint: disable=protected-access  # noqa: SLF001
        exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert exclude.splitlines().count(_file_lock.PLAN_LOCK_IGNORE_PATTERN) == 1
        assert not (root / ".gitignore").exists()
        assert git_repository.git_output(root, "rev-parse", "HEAD") == original_head
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_ensure_environment_keeps_recorded_gitignore_pattern(self, tmp_path: pathlib.Path) -> None:
        """`.gitignore`へ記録済みの管理パターンとユーザーの変更を、commitも削除もしない。"""
        home = tmp_path / "home"
        root = home / "private-notes"
        git_repository.init_repository(root)
        (root / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        (root / ".gitignore").write_text(f"tracked\n{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n", encoding="utf-8")
        git_repository.commit_all(root, "base")
        original_head = git_repository.git_output(root, "rev-parse", "HEAD")
        (root / ".gitignore").write_text(
            f"tracked\nuser-change\n{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n",
            encoding="utf-8",
        )

        assert _wi_sync.ensure_environment(home) == root  # pylint: disable=protected-access  # noqa: SLF001
        assert git_repository.git_output(root, "rev-parse", "HEAD") == original_head
        assert (root / ".gitignore").read_text(encoding="utf-8") == (
            f"tracked\nuser-change\n{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n"
        )


_LEGACY_AWI = "---\ntarget_repo: github.com/example/repo\n---\n\n本文\n"


_LEGACY_UWI = "---\ntarget_repo: github.com/example/repo\nquestion_type: free-form\n---\n\n## 質問\n\nQ\n\n## 回答\n\n"


def _init_legacy_repo(root: pathlib.Path, entries: dict[str, str]) -> None:
    """旧2階層レイアウトのローカル限定リポジトリを`root`へ作成する。

    remote未設定を示すマーカーを置き、移行処理のpull・pushをスキップさせる。
    `entries`はrepo root相対パスと本文の対応とする。
    """
    git_repository.init_repository(root, files={".gitignore": f"{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n", **entries})
    (root / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
    git_repository.commit_all(root, "init")


class TestMigrateLegacyLayout:
    """旧2階層レイアウトから平坦レイアウトへの自動移行を検証する。

    管理repoのパスはconftestが適用する隔離（`agent_toolkit._testing.isolation`）が`tmp_path/private-notes`へ差し替えるため、
    `ensure_environment`へ渡すhomeは解決結果に影響しない。
    """

    def test_migrates_entries_and_removes_legacy_dirs(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """種別ディレクトリ配下のエントリへtypeを補って状態ディレクトリ直下へ移し、旧ディレクトリを削除する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_AWI,
                "feedback/adopted/20260101-000000-002.md": _LEGACY_AWI,
                "tbd/inbox/20260102-000000-001.md": _LEGACY_UWI,
            },
        )

        assert _wi_sync.ensure_environment(tmp_path) == root  # pylint: disable=protected-access  # noqa: SLF001

        assert not (root / "feedback").exists()
        assert not (root / "tbd").exists()
        assert (root / "inbox" / "20260101-000000-001.md").read_text(encoding="utf-8") == (
            "---\ntarget_repo: github.com/example/repo\ntype: awi\n---\n\n本文\n"
        )
        assert (root / "adopted" / "20260101-000000-002.md").read_text(encoding="utf-8").splitlines()[2] == "type: awi"
        assert (root / "inbox" / "20260102-000000-001.md").read_text(encoding="utf-8").splitlines()[1:4] == [
            "target_repo: github.com/example/repo",
            "type: uwi",
            "question_type: free-form",
        ]
        assert "3件を平坦レイアウトへ移行" in capsys.readouterr().err
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_is_noop_after_migration(self, tmp_path: pathlib.Path) -> None:
        """移行後の再実行では追加のコミットを生成しない。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(root, {"feedback/inbox/20260101-000000-001.md": _LEGACY_AWI})
        _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        head = git_repository.git_output(root, "rev-parse", "HEAD")

        _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert git_repository.git_output(root, "rev-parse", "HEAD") == head

    def test_removes_empty_legacy_dirs_without_commit(self, tmp_path: pathlib.Path) -> None:
        """エントリを含まない旧ディレクトリだけがある場合は削除のみで完結する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(root, {"inbox/20260101-000000-001.md": "---\ntarget_repo: r\ntype: awi\n---\n\n本文\n"})
        (root / "feedback" / "inbox").mkdir(parents=True)
        head = git_repository.git_output(root, "rev-parse", "HEAD")

        _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert not (root / "feedback").exists()
        assert git_repository.git_output(root, "rev-parse", "HEAD") == head
        assert not git_repository.git_output(root, "status", "--porcelain")

    def test_aborts_without_changes_when_entry_is_broken(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """frontmatterが不正なエントリがある場合、何も移さずexit 2で原因を案内する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_AWI,
                "feedback/inbox/20260101-000000-002.md": "frontmatterのない本文\n",
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert excinfo.value.code == 2
        assert "frontmatterが不正" in capsys.readouterr().err
        assert (root / "feedback" / "inbox" / "20260101-000000-001.md").exists()
        assert not (root / "inbox").exists()

    def test_aborts_when_destination_conflicts(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """種別違いで同名のエントリがある場合は移行先衝突として中止する。"""
        root = tmp_path / "private-notes"
        _init_legacy_repo(
            root,
            {
                "feedback/inbox/20260101-000000-001.md": _LEGACY_AWI,
                "tbd/inbox/20260101-000000-001.md": _LEGACY_UWI,
            },
        )

        with pytest.raises(SystemExit) as excinfo:
            _wi_sync.ensure_environment(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert excinfo.value.code == 2
        assert "移行先が既に存在" in capsys.readouterr().err


class TestHasRemote:
    """`has_remote`のローカル限定マーカー判定を検証する。"""

    def test_true_when_marker_absent(self, tmp_path: pathlib.Path) -> None:
        """マーカーファイルが無い場合はTrue（通常のremote設定済みリポジトリ扱い）。"""
        assert _wi_sync.has_remote(tmp_path) is True  # pylint: disable=protected-access  # noqa: SLF001

    def test_false_when_marker_present(self, tmp_path: pathlib.Path) -> None:
        """マーカーファイルが存在する場合はFalse（ローカル限定自動生成リポジトリ扱い）。"""
        (tmp_path / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        assert _wi_sync.has_remote(tmp_path) is False  # pylint: disable=protected-access  # noqa: SLF001


class TestPullAndCommitPushSkipWithoutRemote:
    """remote未設定のローカル限定リポジトリではpull・pushをスキップすることを検証する。"""

    def test_pull_is_noop_without_remote(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """マーカー付きディレクトリでは`pull`がremote同期を実行しない。"""
        (tmp_path / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        assert not any(call[0] in ("fetch", "merge") for call in calls)

    def test_commit_and_push_skips_push_without_remote(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """マーカー付きディレクトリでは`commit_and_push`がadd・commitのみ実行しpushしない。"""
        (tmp_path / _wi_sync.LOCAL_ONLY_MARKER).touch()  # pylint: disable=protected-access  # noqa: SLF001
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005
        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.commit_and_push(tmp_path, "chore: test", ["inbox"])  # pylint: disable=protected-access  # noqa: SLF001
        assert calls == [["add", "--all", "--", "inbox"], ["commit", "-m", "chore: test", "--", "inbox"]]


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


class TestPullWithRecentNotice:
    """読み取り専用操作の同期再利用・強制同期・移行契約を検証する。"""

    def test_reuses_recent_sync_when_upstream_is_ancestor(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """直近同期が統合済みの場合はfetch・mergeを省略し、補足を出力しない。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005

        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull_with_recent_reuse(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert [call for call in calls if call[0] in ("fetch", "merge")] == []
        assert not capsys.readouterr().err

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

    def test_force_pull_bypasses_recent_reuse(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """強制同期指定時は直近同期形跡があってもfetch・mergeを実行する。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1010.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005

        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull_with_recent_reuse(tmp_path, force_pull=True)  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]
        assert not capsys.readouterr().err

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

        assert calls == [["fetch"]]
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

        assert calls == [
            ["fetch"],
            ["merge", "--ff-only", "@{u}"],
            ["merge-base", "--is-ancestor", "HEAD", "@{u}"],
            ["merge-base", "--is-ancestor", "@{u}", "HEAD"],
        ]
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

        calls: list[list[str]] = []
        migrations: list[pathlib.Path] = []
        original_run_git = _wi_sync.run_git  # pylint: disable=protected-access  # noqa: SLF001

        def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
            calls.append(args)
            original_run_git(args, cwd, forward_error_output=forward_error_output)

        monkeypatch.setattr(_wi_sync, "run_git", run_git)

        def migrate(private_notes: pathlib.Path) -> int:
            migrations.append(private_notes)
            return 0

        monkeypatch.setattr(_wi_sync, "migrate_legacy_reservations", migrate)
        with _wi_sync.repo_lock(local):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull_with_recent_reuse(local)  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [
            ["merge-base", "--is-ancestor", "@{u}", "HEAD"],
            ["fetch"],
            ["merge", "--ff-only", "@{u}"],
            ["merge-base", "--is-ancestor", "HEAD", "@{u}"],
            ["merge-base", "--is-ancestor", "@{u}", "HEAD"],
            ["rebase", "@{u}"],
        ]
        assert migrations == [local]

    def test_pulls_without_notice_when_fetch_head_is_old(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """30秒以上前の同期形跡では注記せずpullする。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        fetch_head = git_dir / "FETCH_HEAD"
        fetch_head.touch()
        os.utime(fetch_head, (1000.0, 1000.0))
        monkeypatch.setattr(_wi_sync.time, "time", lambda: 1030.0)
        calls: list[list[str]] = []
        monkeypatch.setattr(_wi_sync, "run_git", lambda args, cwd, **_kwargs: calls.append(args))  # noqa: ARG005

        with _wi_sync.repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _wi_sync.pull_with_recent_reuse(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [["fetch"], ["merge", "--ff-only", "@{u}"]]
        assert "同期形跡" not in capsys.readouterr().err
