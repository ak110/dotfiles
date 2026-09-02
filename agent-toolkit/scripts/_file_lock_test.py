"""_file_lock モジュールの単体テスト。

POSIX/NT両分岐のロック取得・解放、`rotate_if_needed`のローテーション動作を検証する。
OS別ロック実装は`_session_state_test.py`の先例に倣い、実行環境のOSと一致する側のみ
`pytest.mark.skipif`で有効化し、実際のロックAPI経由で検証する。
"""

import multiprocessing
import os
import pathlib
import subprocess

import _file_lock
import pytest


def _git(repo: pathlib.Path, *args: str) -> str:
    """テスト用リポジトリでGitを実行し、標準出力を返す。"""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _append_record(path_text: str, record: str, max_bytes: int) -> None:
    """別プロセスから共有ログへテストレコードを追記する。"""
    _file_lock.locked_rotate_and_append(pathlib.Path(path_text), record + "\n", max_bytes)


class TestLockedRotateAndAppend:
    """排他付きローテーションと追記の不可分性を検証する。"""

    def test_concurrent_writers_preserve_all_records_at_rotation_boundary(self, tmp_path: pathlib.Path) -> None:
        """閾値超過状態からの並行追記で全レコードを保持する。"""
        path = tmp_path / "parallel.log"
        max_bytes = 1_000
        path.write_text("x" * (max_bytes + 1), encoding="utf-8")
        records = [f"record-{index}" for index in range(8)]
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_append_record, args=(str(path), record, max_bytes)) for record in records]

        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0

        assert set(path.read_text(encoding="utf-8").splitlines()) == set(records)
        assert path.with_suffix(".log.1").read_text(encoding="utf-8") == "x" * (max_bytes + 1)


class TestEnsurePlanLockIgnored:
    """計画保存先リポジトリの管理ロック除外を検証する。"""

    def test_preserves_existing_content_and_ignores_all_plan_locks(self, tmp_path: pathlib.Path) -> None:
        """既存内容を保持し、root直下と年月階層のロックを除外する。"""
        _git(tmp_path, "init", "-q")
        gitignore = tmp_path / ".gitignore"
        gitignore.write_bytes(b"existing-pattern")
        root_lock = tmp_path / "plans" / ".agent-toolkit-plan-create.lock"
        nested_lock = tmp_path / "plans" / "2026" / "09" / "sample.plan-review.tsv.lock"

        assert _file_lock.ensure_plan_lock_ignored(root_lock)
        assert not _file_lock.ensure_plan_lock_ignored(nested_lock)

        assert gitignore.read_bytes() == b"existing-pattern\n/plans/**/*.lock\n"
        root_lock.parent.mkdir(parents=True)
        nested_lock.parent.mkdir(parents=True)
        root_lock.touch()
        nested_lock.touch()
        assert _git(tmp_path, "check-ignore", str(root_lock.relative_to(tmp_path))).strip() == str(
            root_lock.relative_to(tmp_path)
        )
        assert _git(tmp_path, "check-ignore", str(nested_lock.relative_to(tmp_path))).strip() == str(
            nested_lock.relative_to(tmp_path)
        )

    def test_replaces_legacy_repository_wide_pattern(self, tmp_path: pathlib.Path) -> None:
        """旧版が追記したリポジトリ全体の除外行を`plans/`配下限定の行へ置き換える。"""
        _git(tmp_path, "init", "-q")
        gitignore = tmp_path / ".gitignore"
        gitignore.write_bytes(b"existing-pattern\n*.lock\n")

        assert _file_lock.ensure_plan_lock_ignored(tmp_path / "plans" / ".agent-toolkit-plan-create.lock")

        assert gitignore.read_bytes() == b"existing-pattern\n/plans/**/*.lock\n"
        outside_lock = tmp_path / "state" / "session.lock"
        outside_lock.parent.mkdir(parents=True)
        outside_lock.touch()
        ignored = subprocess.run(
            ["git", "-C", str(tmp_path), "check-ignore", str(outside_lock.relative_to(tmp_path))],
            check=False,
            capture_output=True,
            text=True,
        )
        assert ignored.returncode == 1

    def test_does_not_modify_repository_for_lock_outside_plans(self, tmp_path: pathlib.Path) -> None:
        """`plans/`外の一般ロックでは`.gitignore`を作成しない。"""
        _git(tmp_path, "init", "-q")

        assert not _file_lock.ensure_plan_lock_ignored(tmp_path / "state" / "session.lock")
        assert not (tmp_path / ".gitignore").exists()


class TestRotateIfNeeded:
    """`rotate_if_needed`のローテーション動作。"""

    def test_rotates_when_size_exceeds_max_bytes(self, tmp_path: pathlib.Path) -> None:
        """サイズが上限を超えた場合、`.1`サフィックス付きパスへリネームされる。"""
        path = tmp_path / "sample.log"
        path.write_text("0123456789", encoding="utf-8")

        _file_lock.rotate_if_needed(path, max_bytes=5)

        rotated = tmp_path / "sample.log.1"
        assert rotated.exists()
        assert rotated.read_text(encoding="utf-8") == "0123456789"
        assert not path.exists()

    def test_no_rotation_when_size_within_max_bytes(self, tmp_path: pathlib.Path) -> None:
        """サイズが上限以内の場合はリネームしない。"""
        path = tmp_path / "sample.log"
        path.write_text("short", encoding="utf-8")

        _file_lock.rotate_if_needed(path, max_bytes=1_000)

        assert path.exists()
        assert not (tmp_path / "sample.log.1").exists()

    def test_no_rotation_when_file_missing(self, tmp_path: pathlib.Path) -> None:
        """ファイルが存在しない場合は何もしない（例外を送出しない）。"""
        path = tmp_path / "missing.log"

        _file_lock.rotate_if_needed(path, max_bytes=1)

        assert not path.exists()

    def test_overwrites_existing_generation(self, tmp_path: pathlib.Path) -> None:
        """既存の`.1`世代ファイルは上書きされる。"""
        path = tmp_path / "sample.log"
        path.write_text("new-content-long-enough", encoding="utf-8")
        (tmp_path / "sample.log.1").write_text("old", encoding="utf-8")

        _file_lock.rotate_if_needed(path, max_bytes=1)

        assert (tmp_path / "sample.log.1").read_text(encoding="utf-8") == "new-content-long-enough"

    def test_rejects_multi_generation(self, tmp_path: pathlib.Path) -> None:
        """`generations`に1以外を渡すと`NotImplementedError`を送出する。"""
        path = tmp_path / "sample.log"
        path.write_text("x", encoding="utf-8")

        with pytest.raises(NotImplementedError):
            _file_lock.rotate_if_needed(path, max_bytes=0, generations=2)


@pytest.mark.skipif(os.name == "nt", reason="POSIX固有のロック実装")
class TestLockPosix:
    """POSIX (`fcntl.flock`) のロック取得・解放を確認する。"""

    def test_acquire_and_release_blocking(self, tmp_path: pathlib.Path) -> None:
        """ブロッキング取得・解放が例外なく完了する。"""
        path = tmp_path / "lock"
        with open(path, "a+", encoding="utf-8") as fh:
            _file_lock.acquire_lock(fh)
            _file_lock.release_lock(fh)

    def test_nonblocking_raises_when_already_locked(self, tmp_path: pathlib.Path) -> None:
        """既に排他ロック済みのファイルへ`blocking=False`で取得すると`OSError`を送出する。"""
        path = tmp_path / "lock"
        with open(path, "a+", encoding="utf-8") as fh1, open(path, "a+", encoding="utf-8") as fh2:
            _file_lock.acquire_lock(fh1)
            try:
                with pytest.raises(OSError):
                    _file_lock.acquire_lock(fh2, blocking=False)
            finally:
                _file_lock.release_lock(fh1)


@pytest.mark.skipif(os.name != "nt", reason="Windows固有のロック実装")
class TestLockNt:
    """Windows (`msvcrt.locking`) のロック取得・解放を確認する。"""

    def test_acquire_and_release_blocking(self, tmp_path: pathlib.Path) -> None:
        """ブロッキング取得・解放が例外なく完了する。"""
        path = tmp_path / "lock"
        with open(path, "a+", encoding="utf-8") as fh:
            _file_lock.acquire_lock(fh)
            _file_lock.release_lock(fh)

    def test_nonblocking_raises_when_already_locked(self, tmp_path: pathlib.Path) -> None:
        """既に排他ロック済みのファイルへ`blocking=False`で取得すると`OSError`を送出する。"""
        path = tmp_path / "lock"
        with open(path, "a+", encoding="utf-8") as fh1, open(path, "a+", encoding="utf-8") as fh2:
            _file_lock.acquire_lock(fh1)
            try:
                with pytest.raises(OSError):
                    _file_lock.acquire_lock(fh2, blocking=False)
            finally:
                _file_lock.release_lock(fh1)
