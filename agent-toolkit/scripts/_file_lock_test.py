"""_file_lock モジュールの単体テスト。

POSIX/NT両分岐のロック取得・解放、`rotate_if_needed`のローテーション動作を検証する。
OS別ロック実装は`_session_state_test.py`の先例に倣い、実行環境のOSと一致する側のみ
`pytest.mark.skipif`で有効化し、実際のロックAPI経由で検証する。
"""

import os
import pathlib

import _file_lock
import pytest


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
