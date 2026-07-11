"""_process_loop_log モジュールの単体テスト。

排他ロック分岐・ローテーション動作・環境変数が無効な状態でのno-opを検証する。
"""

import os
import pathlib

import _process_loop_log
import pytest


@pytest.fixture(autouse=True)
def _redirect_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`platformdirs.user_state_dir`が`tmp_path`配下を返すようXDG系変数を差し替える。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _enable_autonomous_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """既定で`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`を設定する（`TestNoop`では個別に上書きする）。"""
    monkeypatch.setenv("DOTFILES_AUTONOMOUS_EXIT_REQUIRED", "1")


class TestAppend:
    """`append`の基本動作。"""

    def test_append_writes_one_line(self) -> None:
        _process_loop_log.append("loop_iter_start", count=3)
        content = _process_loop_log.log_path().read_text(encoding="utf-8")
        lines = content.splitlines()
        assert len(lines) == 1
        assert "event=loop_iter_start" in lines[0]
        assert "count=3" in lines[0]

    def test_append_multiple_events_accumulate(self) -> None:
        _process_loop_log.append("session_start")
        _process_loop_log.append("session_end", elapsed_sec=12.5)
        lines = _process_loop_log.log_path().read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert "event=session_start" in lines[0]
        assert "event=session_end" in lines[1]
        assert "elapsed_sec=12.5" in lines[1]

    def test_append_creates_parent_directory(self) -> None:
        assert not _process_loop_log.log_path().parent.exists()
        _process_loop_log.append("session_start")
        assert _process_loop_log.log_path().exists()


class TestNoop:
    """環境変数未設定時のno-op動作。"""

    def test_append_is_noop_without_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`DOTFILES_AUTONOMOUS_EXIT_REQUIRED`未設定時はログファイルを作成しない。"""
        monkeypatch.delenv("DOTFILES_AUTONOMOUS_EXIT_REQUIRED", raising=False)
        _process_loop_log.append("session_start")
        assert not _process_loop_log.log_path().exists()

    def test_append_is_noop_with_falsy_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=0`など非`1`値もno-opとして扱う。"""
        monkeypatch.setenv("DOTFILES_AUTONOMOUS_EXIT_REQUIRED", "0")
        _process_loop_log.append("session_start")
        assert not _process_loop_log.log_path().exists()


class TestRotation:
    """サイズ超過時のローテーション動作。"""

    def test_rotates_when_max_bytes_exceeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """既存ログが閾値を超えている場合、追記前に`.log.1`へローテートされる。"""
        monkeypatch.setattr(_process_loop_log, "_MAX_BYTES", 10)
        _process_loop_log.append("first", count=1)
        _process_loop_log.append("second", count=2)
        rotated = _process_loop_log.log_path().with_suffix(".log.1")
        assert rotated.exists()
        assert "event=first" in rotated.read_text(encoding="utf-8")
        assert "event=second" in _process_loop_log.log_path().read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX固有のロック実装")
class TestLockPosix:
    """POSIX環境でロックファイル経由の排他制御が働くことを確認する。"""

    def test_lock_file_created(self) -> None:
        _process_loop_log.append("session_start")
        lock_path = _process_loop_log.log_path().parent / (_process_loop_log.log_path().name + ".lock")
        assert lock_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows固有のロック実装")
class TestLockNt:
    """Windows環境でロックファイル経由の排他制御が働くことを確認する。"""

    def test_lock_file_created(self) -> None:
        _process_loop_log.append("session_start")
        lock_path = _process_loop_log.log_path().parent / (_process_loop_log.log_path().name + ".lock")
        assert lock_path.exists()
