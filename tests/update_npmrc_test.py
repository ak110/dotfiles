"""pytools.update_npmrc のテスト。"""

from pathlib import Path

from pytools.update_npmrc import run


class TestUpdateNpmrc:
    """~/.npmrc へのサプライチェーン対策設定反映テスト。"""

    def test_new_file_is_created(self, tmp_path: Path):
        """対象ファイルが存在しない場合、新規作成して設定を書き込む。"""
        path = tmp_path / ".npmrc"
        assert run(path) is True
        assert path.read_text(encoding="utf-8") == "minimum-release-age=1440\n"

    def test_existing_line_is_replaced(self, tmp_path: Path):
        """既存の minimum-release-age 行があれば値を置換する。"""
        path = tmp_path / ".npmrc"
        path.write_text("registry=https://example.com/\nminimum-release-age=60\n", encoding="utf-8")
        assert run(path) is True
        content = path.read_text(encoding="utf-8")
        assert "minimum-release-age=1440" in content
        assert "registry=https://example.com/" in content
        assert "minimum-release-age=60" not in content

    def test_matching_line_is_noop(self, tmp_path: Path):
        """既に 1440 が設定されていればファイル書き換えなし。"""
        path = tmp_path / ".npmrc"
        path.write_text("minimum-release-age=1440\n", encoding="utf-8")
        mtime_before = path.stat().st_mtime_ns
        assert run(path) is False
        assert path.stat().st_mtime_ns == mtime_before

    def test_key_missing_is_appended(self, tmp_path: Path):
        """キーが存在しない場合は末尾に追記する (既存行は保持)。"""
        path = tmp_path / ".npmrc"
        path.write_text("registry=https://example.com/\n", encoding="utf-8")
        assert run(path) is True
        content = path.read_text(encoding="utf-8")
        assert content.startswith("registry=https://example.com/")
        assert content.rstrip().endswith("minimum-release-age=1440")

    def test_missing_trailing_newline(self, tmp_path: Path):
        """既存ファイルの末尾改行が無くても正しく追記される。"""
        path = tmp_path / ".npmrc"
        path.write_text("registry=https://example.com/", encoding="utf-8")
        assert run(path) is True
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert lines[0] == "registry=https://example.com/"
        assert lines[1] == "minimum-release-age=1440"
