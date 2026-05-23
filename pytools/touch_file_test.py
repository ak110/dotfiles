"""touch_file モジュールのテスト。"""

import datetime
import os
import pathlib
import subprocess
import sys

import pytest

from pytools import touch_file


def _set_mtime(path: pathlib.Path, time: datetime.datetime) -> None:
    ts = time.timestamp()
    os.utime(path, (ts, ts))


def _read_mtime(path: pathlib.Path) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime)


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

    @pytest.mark.skipif(sys.platform == "win32", reason="シンボリックリンクの作成に管理者権限が必要なため")
    def test_symlink_does_not_follow(self, tmp_path: pathlib.Path) -> None:
        real_file = tmp_path / "real.txt"
        real_file.touch()
        link = tmp_path / "link"
        link.symlink_to(real_file)

        old_real = datetime.datetime(2000, 1, 1)
        _set_mtime(real_file, old_real)

        new_time = datetime.datetime(2024, 6, 15, 12, 30, 45)
        touch_file.touch_path(link, new_time)

        # リンク自身を touch してもリンク先のファイルは変更されない
        assert _read_mtime(real_file) == old_real


class TestCli:
    """CLI 経由の動作テスト。"""

    def _run(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytools.touch_file", *args],
            capture_output=True,
            text=True,
            input=stdin,
            check=True,
        )

    def test_cli_t_option_with_single_file(self, tmp_path: pathlib.Path) -> None:
        target = tmp_path / "f.txt"
        target.touch()
        _set_mtime(target, datetime.datetime(2000, 1, 1))

        self._run("-t", "2024-06-15T12:30:45", str(target))

        assert _read_mtime(target) == datetime.datetime(2024, 6, 15, 12, 30, 45)

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
