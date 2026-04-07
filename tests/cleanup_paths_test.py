"""pytools.cleanup_paths のテスト。"""

from pathlib import Path

from pytools.cleanup_paths import cleanup_paths


class TestCleanupPaths:
    """汎用「旧配布物の削除」関数のテスト。"""

    def test_directory_is_removed(self, tmp_path: Path):
        """指定パスのディレクトリが再帰的に削除される。"""
        target = tmp_path / "skills" / "old-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body", encoding="utf-8")

        removed = cleanup_paths(tmp_path, (Path("skills/old-skill"),))

        assert removed == 1
        assert not target.exists()

    def test_file_is_removed(self, tmp_path: Path):
        """単一ファイルも削除できる。"""
        target = tmp_path / "old.txt"
        target.write_text("body", encoding="utf-8")

        removed = cleanup_paths(tmp_path, (Path("old.txt"),))

        assert removed == 1
        assert not target.exists()

    def test_missing_path_is_noop(self, tmp_path: Path):
        """対象が存在しなくてもエラーにならず、削除件数 0。"""
        removed = cleanup_paths(tmp_path, (Path("skills/missing"),))
        assert removed == 0

    def test_missing_base_dir_is_noop(self, tmp_path: Path):
        """base_dir 自体が存在しなくても安全。"""
        removed = cleanup_paths(tmp_path / "not_exists", (Path("a"),))
        assert removed == 0

    def test_path_outside_base_is_skipped(self, tmp_path: Path):
        """シンボリックリンク経由で base_dir 外を削除しようとしたらスキップする。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "do_not_delete.txt").write_text("important", encoding="utf-8")
        base = tmp_path / "claude"
        base.mkdir()
        (base / "linked").symlink_to(outside)

        cleanup_paths(base, (Path("linked"),))

        # シンボリックリンク先は base 外なので守られる
        assert outside.exists()
        assert (outside / "do_not_delete.txt").exists()

    def test_multiple_paths_counted(self, tmp_path: Path):
        """削除件数は実際に消したものだけをカウントする。"""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").write_text("", encoding="utf-8")
        # c は存在しない
        removed = cleanup_paths(tmp_path, (Path("a"), Path("b"), Path("c")))
        assert removed == 2
