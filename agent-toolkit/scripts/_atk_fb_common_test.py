"""`atk fb`共通の警告・通知処理を検証する。"""

import pathlib
import subprocess
import sys
import threading
import time

import filelock
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_fb_common as _common  # noqa: E402  # pylint: disable=wrong-import-position


def _write_tbd(
    private_notes: pathlib.Path,
    filename: str,
    *,
    target_repo: str = "github.com/example/repo",
    question: str = "確認事項",
    answer: str = "",
) -> None:
    """テスト用TBDをinboxへ書き込む。"""
    inbox = private_notes / "tbd" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / filename).write_text(
        f"---\ntarget_repo: {target_repo}\n---\n\n## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )


class TestWarnSpaceSeparatedOption:
    """空白区切りオプションの検出条件を検証する。"""

    @pytest.mark.parametrize(
        "top_command,subcommand",
        [("fb", "adopt"), ("fb", "reject"), ("tb", "adopt")],
    )
    @pytest.mark.parametrize("option", ["--note", "--commit"])
    def test_warns_for_target_subcommands(
        self,
        top_command: str,
        subcommand: str,
        option: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象サブコマンドの空白区切り指定では推奨形式を警告する。"""
        _common.warn_space_separated_option([top_command, subcommand, "item.md", option, "value"])

        assert capsys.readouterr().err == f"警告: {option}は{option}=VALUE形式で渡すことを推奨します。\n"

    @pytest.mark.parametrize(
        "argv",
        [
            ["fb", "rm", "item.md", "--note", "value"],
            ["fb", "add", "/repo", "adopt", "--note", "value"],
            ["fb", "adopt", "item.md", "--note=value"],
            ["fb", "adopt", "item.md", "--note", "value=with-equals"],
            ["fb", "adopt", "item.md", "--note", "--target-repo=example/repo"],
        ],
    )
    def test_does_not_warn_for_excluded_forms(
        self,
        argv: list[str],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """対象外サブコマンド・等号形式・次オプションでは警告しない。"""
        _common.warn_space_separated_option(argv)

        assert not capsys.readouterr().err


class TestNotifyUnansweredTbdsIfAny:
    """未回答TBD通知の件数・フィルター・形式を検証する。"""

    def test_does_not_notify_without_unanswered_entries(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """TBDが0件または全件回答済みの場合は何も通知しない。"""
        _write_tbd(tmp_path, "answered.md", answer="回答済み")

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        assert not capsys.readouterr().err

    def test_notifies_one_unanswered_entry(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """未回答TBDが1件の場合はヘッダと1行を通知する。"""
        _write_tbd(tmp_path, "one.md", question="最初の質問")

        _common.notify_unanswered_tbds_if_any(tmp_path, None)

        assert capsys.readouterr().err == "# tbd\none.md: github.com/example/repo [unanswered] 最初の質問\n"

    def test_notifies_matching_unanswered_entries_in_filename_order(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """複数件では対象リポジトリの未回答項目だけをファイル名順で通知する。"""
        _write_tbd(tmp_path, "002.md", question="質問2")
        _write_tbd(tmp_path, "001.md", question="質問1")
        _write_tbd(tmp_path, "003.md", target_repo="github.com/example/other", question="対象外")

        _common.notify_unanswered_tbds_if_any(tmp_path, "github.com/example/repo")

        assert capsys.readouterr().err == (
            "# tbd\n001.md: github.com/example/repo [unanswered] 質問1\n002.md: github.com/example/repo [unanswered] 質問2\n"
        )


class TestIsExistingDir:
    """長大な文字列候補に対する`is_existing_dir`のOSError耐性を検証する。"""

    def test_returns_true_for_existing_directory(self, tmp_path: pathlib.Path) -> None:
        """実在ディレクトリはTrueを返す。"""
        assert _common.is_existing_dir(tmp_path) is True

    def test_returns_false_for_missing_path(self, tmp_path: pathlib.Path) -> None:
        """存在しないパスはFalseを返す。"""
        assert _common.is_existing_dir(tmp_path / "missing") is False

    def test_returns_false_for_oversized_name_without_raising(self) -> None:
        """OS上限を超える長さの文字列でも`OSError`を送出せずFalseを返す。"""
        oversized = pathlib.Path("x" * 5000)

        assert _common.is_existing_dir(oversized) is False


class TestRepoLock:
    """`_repo_lock`のプロセス間排他動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(_common.platformdirs, "user_state_dir", lambda _name: str(tmp_path / "state"))

    def test_second_acquire_times_out_while_held(self, tmp_path: pathlib.Path) -> None:
        """1つ目のロック保持中は、別インスタンスからの2つ目の取得がタイムアウトする。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        lock1 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        lock1.acquire()
        try:
            lock2 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
            with pytest.raises(filelock.Timeout):
                lock2.acquire(timeout=0.2)
        finally:
            lock1.release()

    def test_second_acquire_succeeds_after_release(self, tmp_path: pathlib.Path) -> None:
        """1つ目のロック解放後は、別インスタンスからの2つ目の取得が成功する。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        with _common._repo_lock(target):  # pylint: disable=protected-access  # noqa: SLF001
            pass
        lock2 = _common._repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        with lock2:
            assert lock2.is_locked

    def test_concurrent_transactions_are_serialized(self, tmp_path: pathlib.Path) -> None:
        """2スレッドが同時に`_repo_lock`を取得しても、臨界区間が直列化されること。"""
        target = tmp_path / "private-notes"
        target.mkdir()
        order: list[str] = []

        def worker(label: str) -> None:
            with _common._repo_lock(target):  # pylint: disable=protected-access  # noqa: SLF001
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
    """`_assert_repo_lock_held`の不変条件表明を検証する。"""

    def test_pull_raises_runtime_error_when_lock_not_held(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock`未保持で`_pull`を呼ぶと`RuntimeError`を送出する。"""
        with pytest.raises(RuntimeError, match="不変条件違反"):
            _common._pull(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    def test_commit_and_push_raises_runtime_error_when_lock_not_held(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock`未保持で`_commit_and_push`を呼ぶと`RuntimeError`を送出する。"""
        with pytest.raises(RuntimeError, match="不変条件違反"):
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001


class TestCommitAndPushRetry:
    """`_commit_and_push`のpush失敗時再試行動作を検証する。"""

    @pytest.fixture(autouse=True)
    def _isolate_lock_dir(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ロックファイル配置先を実環境の`user_state_dir`から隔離する。"""
        monkeypatch.setattr(_common.platformdirs, "user_state_dir", lambda _name: str(tmp_path / "state"))

    def test_retries_once_after_pull_rebase_on_push_failure(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """push失敗時は`pull --rebase`実行後にpushを1回だけ再試行する。"""
        calls: list[list[str]] = []
        push_attempts = 0

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            nonlocal push_attempts
            del cwd
            calls.append(args)
            if args[0] == "push":
                push_attempts += 1
                if push_attempts == 1:
                    raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_common, "_run_git", fake_run_git)

        with _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

        assert calls == [
            ["add", "feedback"],
            ["commit", "-m", "chore: test"],
            ["push"],
            ["pull", "--rebase"],
            ["push"],
        ]

    def test_reraises_when_retry_push_also_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """再試行後もpushが失敗した場合は例外をそのまま送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push":
                raise subprocess.CalledProcessError(1, ["git", *args])

        monkeypatch.setattr(_common, "_run_git", fake_run_git)

        with (
            pytest.raises(subprocess.CalledProcessError),
            _common._repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

    def test_aborts_rebase_and_reports_success_when_pull_rebase_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """経由した`pull --rebase`が失敗した場合は`git rebase --abort`を呼び、
        復元成功をstderrへ出力してから例外を送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push" or args == ["pull", "--rebase"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        abort_calls: list[list[str]] = []

        def fake_subprocess_run(args: list[str], cwd: pathlib.Path, check: bool) -> subprocess.CompletedProcess[bytes]:
            del cwd
            assert check is False
            abort_calls.append(args)
            return subprocess.CompletedProcess(args, returncode=0)

        monkeypatch.setattr(_common, "_run_git", fake_run_git)
        monkeypatch.setattr(_common.subprocess, "run", fake_subprocess_run)

        with pytest.raises(subprocess.CalledProcessError), _common._repo_lock(tmp_path):  # pylint: disable=protected-access  # noqa: SLF001
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

        assert abort_calls == [["git", "rebase", "--abort"]]
        assert "復元しました" in capsys.readouterr().err

    def test_warns_manual_recovery_when_rebase_abort_fails(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`git rebase --abort`自体が失敗した場合は手動復旧が必要な旨をstderrへ出力してから例外を送出する。"""

        def fake_run_git(args: list[str], cwd: pathlib.Path) -> None:
            del cwd
            if args[0] == "push" or args == ["pull", "--rebase"]:
                raise subprocess.CalledProcessError(1, ["git", *args])

        def fake_subprocess_run(args: list[str], cwd: pathlib.Path, check: bool) -> subprocess.CompletedProcess[bytes]:
            del cwd
            assert check is False
            return subprocess.CompletedProcess(args, returncode=1)

        monkeypatch.setattr(_common, "_run_git", fake_run_git)
        monkeypatch.setattr(_common.subprocess, "run", fake_subprocess_run)

        with (
            pytest.raises(subprocess.CalledProcessError),
            _common._repo_lock(tmp_path),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            _common._commit_and_push(tmp_path, "chore: test", ["feedback"])  # pylint: disable=protected-access  # noqa: SLF001

        assert "手動復旧が必要です" in capsys.readouterr().err
