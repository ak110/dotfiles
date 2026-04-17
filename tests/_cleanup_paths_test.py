"""pytools._cleanup_paths のテスト。"""

from pathlib import Path

from pytools._cleanup_paths import cleanup_paths, cleanup_paths_if_content_matches


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


class TestCleanupPathsIfContentMatches:
    """内容一致時のみ削除する関数のテスト。"""

    def test_matching_file_is_removed(self, tmp_path: Path):
        """期待内容と一致するファイルは削除される。"""
        target = tmp_path / "CLAUDE.md"
        content = b"# \xe3\x82\xab\xe3\x82\xb9\xe3\x82\xbf\xe3\x83\xa0\n"
        target.write_bytes(content)

        removed = cleanup_paths_if_content_matches(tmp_path, {Path("CLAUDE.md"): content})

        assert removed == 1
        assert not target.exists()

    def test_modified_file_is_kept(self, tmp_path: Path):
        """内容が異なるファイルは削除されず残る。"""
        target = tmp_path / "CLAUDE.md"
        target.write_bytes(b"user edited\n")

        removed = cleanup_paths_if_content_matches(tmp_path, {Path("CLAUDE.md"): b"original\n"})

        assert removed == 0
        assert target.exists()
        assert target.read_bytes() == b"user edited\n"

    def test_missing_path_is_noop(self, tmp_path: Path):
        """対象が存在しなくてもエラーにならず、削除件数 0。"""
        removed = cleanup_paths_if_content_matches(tmp_path, {Path("missing.md"): b"x"})
        assert removed == 0

    def test_missing_base_dir_is_noop(self, tmp_path: Path):
        """base_dir 自体が存在しなくても安全。"""
        removed = cleanup_paths_if_content_matches(tmp_path / "not_exists", {Path("a"): b"x"})
        assert removed == 0

    def test_symlink_outside_base_is_skipped(self, tmp_path: Path):
        """シンボリックリンク経由で base_dir 外を削除しようとしたらスキップする。"""
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_file = outside / "secret.md"
        outside_file.write_bytes(b"payload\n")
        base = tmp_path / "claude"
        base.mkdir()
        (base / "CLAUDE.md").symlink_to(outside_file)

        removed = cleanup_paths_if_content_matches(base, {Path("CLAUDE.md"): b"payload\n"})

        assert removed == 0
        assert outside_file.exists()

    def test_counts_only_matching(self, tmp_path: Path):
        """一致・不一致・欠損が混在しても、一致したものだけが件数に含まれる。"""
        (tmp_path / "match.md").write_bytes(b"ok\n")
        (tmp_path / "mismatch.md").write_bytes(b"edited\n")
        # missing.md は存在しない

        removed = cleanup_paths_if_content_matches(
            tmp_path,
            {
                Path("match.md"): b"ok\n",
                Path("mismatch.md"): b"ok\n",
                Path("missing.md"): b"ok\n",
            },
        )

        assert removed == 1
        assert not (tmp_path / "match.md").exists()
        assert (tmp_path / "mismatch.md").exists()
