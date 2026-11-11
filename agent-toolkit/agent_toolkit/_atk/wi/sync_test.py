# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのsync: baseのテスト。"""

import pathlib
import threading
import time

import filelock
import platformdirs
import pytest

from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.wi_sync_support import _remote_sync as _remote_sync


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
        with lock1:
            lock2 = _wi_sync.repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
            with pytest.raises(filelock.Timeout):
                lock2.acquire(timeout=0.2)

    def test_constructor_timeout_bounds_plain_with_statement(self, tmp_path: pathlib.Path) -> None:
        """`_repo_lock(..., timeout=...)`のコンストラクタで設定した値が`with lock:`（引数無し取得）へ伝搬する。

        Web要求を処理する際は`acquire(timeout=...)`を明示呼び出しせず`with _repo_lock(private_notes, timeout=...):`
        の形でロックを使うため、コンストラクタで指定した`timeout`が実際の`with`文へ反映されることを保証する。
        """
        target = tmp_path / "private-notes"
        target.mkdir()
        lock1 = _wi_sync.repo_lock(target)  # pylint: disable=protected-access  # noqa: SLF001
        with (
            lock1,
            pytest.raises(filelock.Timeout),
            _wi_sync.repo_lock(target, timeout=0.2),  # pylint: disable=protected-access  # noqa: SLF001
        ):
            pass

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
