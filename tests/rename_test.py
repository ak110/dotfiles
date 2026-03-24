"""renameモジュールのテスト。"""

import pathlib
import subprocess
import sys


class TestRename:
    """py-renameコマンドのテスト。"""

    def _run(self, *args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pytools.rename", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=True,
        )

    def test_stem_rename(self, tmp_path):
        (tmp_path / "foo_bar.txt").touch()
        (tmp_path / "foo_baz.txt").touch()
        self._run("foo", "qux", str(tmp_path / "foo_bar.txt"), str(tmp_path / "foo_baz.txt"), cwd=tmp_path)
        assert (tmp_path / "qux_bar.txt").exists()
        assert (tmp_path / "qux_baz.txt").exists()

    def test_name_rename(self, tmp_path):
        (tmp_path / "hello.txt").touch()
        self._run("--name", r"\.txt$", ".md", str(tmp_path / "hello.txt"), cwd=tmp_path)
        assert (tmp_path / "hello.md").exists()
        assert not (tmp_path / "hello.txt").exists()

    def test_dry_run(self, tmp_path):
        (tmp_path / "original.txt").touch()
        self._run("--dry-run", "original", "renamed", str(tmp_path / "original.txt"), cwd=tmp_path)
        # dry-runではファイルがリネームされない
        assert (tmp_path / "original.txt").exists()
        assert not (tmp_path / "renamed.txt").exists()

    def test_ignore_case(self, tmp_path):
        (tmp_path / "FOO.txt").touch()
        self._run("--ignore-case", "foo", "bar", str(tmp_path / "FOO.txt"), cwd=tmp_path)
        assert (tmp_path / "bar.txt").exists()
