"""pytools._internal.file_lock のテスト。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pytools._internal import file_lock

# 別プロセスからロックを取得してマーカーファイルを作成する子プロセス用スクリプト。
_CHILD_SCRIPT = """
import pathlib
import sys

from pytools._internal import file_lock

lock_path = pathlib.Path(sys.argv[1])
marker = pathlib.Path(sys.argv[2])
with file_lock.exclusive_file_lock(lock_path):
    marker.write_text("acquired", encoding="utf-8")
"""

# 子プロセスがロック待ちで滞留していることを確認する観測時間（秒）。
_BLOCKED_OBSERVATION_SEC = 0.5
# ロック解放後に子プロセスの完了を待つ上限（秒）。
_CHILD_COMPLETION_TIMEOUT_SEC = 30.0


class TestExclusiveFileLock:
    """`exclusive_file_lock`の契約を検証する。"""

    def test_creates_lock_file_and_allows_reacquisition(self, tmp_path: Path):
        """コンテキスト内でロックファイルが作成され、終了後に再取得できること。"""
        lock_path = tmp_path / "nested" / "index.json.lock"

        with file_lock.exclusive_file_lock(lock_path):
            assert lock_path.is_file()

        with file_lock.exclusive_file_lock(lock_path):
            assert lock_path.is_file()

    def test_releases_lock_after_exception(self, tmp_path: Path):
        """コンテキスト内の例外発生後もロックが解放されること。"""
        lock_path = tmp_path / "index.json.lock"

        with pytest.raises(RuntimeError, match="想定内の失敗"), file_lock.exclusive_file_lock(lock_path):
            raise RuntimeError("想定内の失敗")

        # 解放済みでなければ以降の取得が返らないため、再取得の成立が解放の証拠となる。
        with file_lock.exclusive_file_lock(lock_path):
            assert lock_path.is_file()

    def test_blocks_other_process_until_released(self, tmp_path: Path):
        """保持中は別プロセスが進入できず、解放後に進入できること。"""
        lock_path = tmp_path / "index.json.lock"
        marker = tmp_path / "marker.txt"
        # 子プロセスから`pytools`をimportできるよう、現在の探索パスを引き継ぐ。
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))

        proc: subprocess.Popen[bytes] | None = None
        try:
            # 先にロックを保持してから子プロセスを起動し、取得順序を確定させる。
            with file_lock.exclusive_file_lock(lock_path):
                proc = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
                    [sys.executable, "-c", _CHILD_SCRIPT, str(lock_path), str(marker)],
                    env=env,
                )
                # 子プロセスがロック待ちで完了しないこと（進入できれば`wait`が即座に返る）。
                with pytest.raises(subprocess.TimeoutExpired):
                    proc.wait(timeout=_BLOCKED_OBSERVATION_SEC)
                assert not marker.exists()
            assert proc.wait(timeout=_CHILD_COMPLETION_TIMEOUT_SEC) == 0
        finally:
            if proc is not None:
                proc.kill()

        assert marker.read_text(encoding="utf-8") == "acquired"
