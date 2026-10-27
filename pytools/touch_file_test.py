"""touch_file モジュールのテスト。"""

import ctypes
import datetime
import os
import pathlib
import subprocess
import sys
from unittest.mock import Mock

import pytest

from pytools import touch_file
from pytools._internal import winutils


def _set_mtime(path: pathlib.Path, time: datetime.datetime) -> None:
    ts = time.timestamp()
    os.utime(path, (ts, ts))


def _read_mtime(path: pathlib.Path) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime)


def _read_link_mtime(path: pathlib.Path) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(path.lstat().st_mtime)


class TestTouchPath:
    """touch_path のロジック単体テスト。"""

    def test_single_file_updates_mtime(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "f.txt"
        target.touch()
        _set_mtime(target, datetime.datetime(2000, 1, 1))

        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        touch_file.touch_path(target, new_time)

        assert _read_mtime(target) == new_time

    def test_directory_recursive(self, tmp_path: pathlib.Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "inner.txt").touch()
        (tmp_path / "top.txt").touch()

        old = datetime.datetime(2000, 1, 1)
        for p in [tmp_path, sub, sub / "inner.txt", tmp_path / "top.txt"]:
            _set_mtime(p, old)

        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        touch_file.touch_path(tmp_path, new_time)

        assert _read_mtime(tmp_path) == new_time
        assert _read_mtime(sub) == new_time
        assert _read_mtime(sub / "inner.txt") == new_time
        assert _read_mtime(tmp_path / "top.txt") == new_time

    def test_missing_path_logs_warning(self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture) -> None:
        missing = tmp_path / "nope"
        with caplog.at_level("WARNING", logger=touch_file.logger.name):
            touch_file.touch_path(missing, datetime.datetime(2024, 1, 1))
        assert any("存在しません" in r.message for r in caplog.records)

    def test_symlink_does_not_follow(self, tmp_path: pathlib.Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.touch()
        link = tmp_path / "link"
        try:
            link.symlink_to(real_file)
        except OSError as error:
            pytest.skip(f"シンボリックリンクを作成できない環境: {error}")

        old_real = datetime.datetime(2000, 1, 1)
        _set_mtime(real_file, old_real)

        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        touch_file.touch_path(link, new_time)

        # リンク自身を touch してもリンク先のファイルは変更されない
        assert _read_mtime(real_file) == old_real
        assert _read_link_mtime(link) == new_time

    def test_symlink_uses_fallback_without_follow_symlinks(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_file = tmp_path / "real.txt"
        real_file.touch()
        link = tmp_path / "link"
        try:
            link.symlink_to(real_file)
        except OSError as error:
            pytest.skip(f"シンボリックリンクを作成できない環境: {error}")

        calls: list[tuple[str, float]] = []

        def set_link_times(path: str, timestamp: float) -> None:
            calls.append((path, timestamp))

        monkeypatch.setattr(touch_file.os, "supports_follow_symlinks", set())
        monkeypatch.setattr(touch_file.winutils, "set_link_times", set_link_times, raising=False)
        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        touch_file.touch_path(link, new_time)

        assert calls == [(str(link), new_time.timestamp())]

    @pytest.mark.skipif(sys.platform != "win32", reason="ディレクトリジャンクションはWindows固有")
    def test_junction_does_not_follow(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        target_file = target / "inner.txt"
        target_file.touch()
        junction = root / "junction"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True)
        assert junction.is_junction()

        old = datetime.datetime(2000, 1, 1)
        _set_mtime(target, old)
        _set_mtime(target_file, old)

        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        touch_file.touch_path(root, new_time)

        assert _read_mtime(target) == old
        assert _read_mtime(target_file) == old
        assert _read_link_mtime(junction) == new_time


class _FakeWin32Function:
    """Win32関数の呼出しとctypes属性設定を再現する。"""

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: object | None = None
        self.restype: object | None = None

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.result


class _FakeKernel32:
    """`set_link_times`が使うWin32 APIだけを再現する。"""

    def __init__(self, create_result: object, set_result: object) -> None:
        self.CreateFileW = _FakeWin32Function(create_result)
        self.SetFileTime = _FakeWin32Function(set_result)
        self.CloseHandle = _FakeWin32Function(1)


def _fake_win_error(code: int) -> OSError:
    return OSError(code, f"fake Windows error {code}")


class TestSetLinkTimes:
    """Win32 API失敗時の診断とハンドル解放を検証する。"""

    def test_create_file_failure_raises_last_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        invalid_handle = ctypes.c_void_p(-1).value
        kernel32 = _FakeKernel32(invalid_handle, 1)
        monkeypatch.setattr(winutils, "_load_kernel32", lambda _: kernel32)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
        monkeypatch.setattr(ctypes, "WinError", _fake_win_error, raising=False)

        with pytest.raises(OSError, match="fake Windows error 5") as error:
            winutils.set_link_times("link", 0)

        assert error.value.errno == 5
        assert not kernel32.SetFileTime.calls
        assert not kernel32.CloseHandle.calls

    def test_set_file_time_failure_closes_handle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        handle = 123
        kernel32 = _FakeKernel32(handle, 0)
        monkeypatch.setattr(winutils, "_load_kernel32", lambda _: kernel32)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 32, raising=False)
        monkeypatch.setattr(ctypes, "WinError", _fake_win_error, raising=False)

        with pytest.raises(OSError, match="fake Windows error 32") as error:
            winutils.set_link_times("link", 0)

        assert error.value.errno == 32
        assert len(kernel32.SetFileTime.calls) == 1
        assert kernel32.CloseHandle.calls == [(handle,)]


class TestCli:
    """CLI 経由の動作テスト。"""

    def _run(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytools.touch_file", *args],
            capture_output=True,
            text=True,
            input=stdin,
            check=True,
            timeout=15,
        )

    @pytest.mark.parametrize("terminal", [False, True])
    def test_windows_interactive_waits_only_for_terminal(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, terminal: bool
    ) -> None:
        target = tmp_path / "f.txt"
        target.touch()
        wait = Mock()
        monkeypatch.setattr(sys, "argv", ["touch-file", str(target)])
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: terminal)
        monkeypatch.setattr("builtins.input", lambda _: "2024-06-15T12:30:45")
        monkeypatch.setattr(touch_file, "_wait_keypress", wait)

        with pytest.raises(SystemExit) as exited:
            touch_file.main()

        assert exited.value.code == 0
        assert _read_mtime(target) == datetime.datetime(2024, 6, 15, 12, 30, 45)
        assert wait.call_count == int(terminal)

    def test_cli_t_option_with_single_file(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "f.txt"
        target.touch()
        _set_mtime(target, datetime.datetime(2000, 1, 1))

        self._run("-t", "2024-06-15T12:30:45", str(target))

        assert _read_mtime(target) == datetime.datetime(2024, 6, 15, 12, 30, 45)

    def test_cli_symlink_does_not_follow(self, tmp_path: pathlib.Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.touch()
        link = tmp_path / "link"
        try:
            link.symlink_to(real_file)
        except OSError as error:
            pytest.skip(f"シンボリックリンクを作成できない環境: {error}")

        old_real = datetime.datetime(2000, 1, 1)
        _set_mtime(real_file, old_real)

        self._run("-t", "2024-06-15T12:30:45", str(link))

        assert _read_mtime(real_file) == old_real
        assert _read_link_mtime(link) == datetime.datetime(2024, 6, 15, 12, 30, 45)

    def test_cli_with_multiple_targets_mixed(self, tmp_path: pathlib.Path) -> None:
        file1 = tmp_path / "f1.txt"
        file2 = tmp_path / "f2.txt"
        sub = tmp_path / "sub"
        sub.mkdir()
        inner = sub / "inner.txt"
        for p in [file1, file2, inner]:
            p.touch()

        old = datetime.datetime(2000, 1, 1)
        for p in [file1, file2, sub, inner]:
            _set_mtime(p, old)

        self._run("-t", "2024-06-15T12:30:45", str(file1), str(file2), str(sub))

        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        assert _read_mtime(file1) == new_time
        assert _read_mtime(file2) == new_time
        assert _read_mtime(sub) == new_time
        assert _read_mtime(inner) == new_time

    def test_cli_continues_on_missing_path(self, tmp_path: pathlib.Path) -> None:
        existing = tmp_path / "f.txt"
        existing.touch()
        _set_mtime(existing, datetime.datetime(2000, 1, 1))
        missing = tmp_path / "nope"

        self._run("-t", "2024-06-15T12:30:45", str(missing), str(existing))

        # 存在しないパスがあっても後続の対象は更新される
        assert _read_mtime(existing) == datetime.datetime(2024, 6, 15, 12, 30, 45)

    def test_cli_interactive_empty_input_uses_now(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "f.txt"
        target.touch()
        _set_mtime(target, datetime.datetime(2000, 1, 1))

        before = datetime.datetime.now()
        self._run(str(target), stdin="\n")
        after = datetime.datetime.now()

        actual = _read_mtime(target)
        assert before - datetime.timedelta(seconds=2) <= actual <= after + datetime.timedelta(seconds=2)

    def test_cli_interactive_with_space_separated_input(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "f.txt"
        target.touch()
        _set_mtime(target, datetime.datetime(2000, 1, 1))

        # 空白区切りの旧形式 (YYYY-MM-DD HH:MM:SS) も fromisoformat が受け付ける
        self._run(str(target), stdin="2024-06-15 12:30:45\n")

        assert _read_mtime(target) == datetime.datetime(2024, 6, 15, 12, 30, 45)
