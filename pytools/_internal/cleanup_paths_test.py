import hashlib
import logging
from pathlib import Path

import pytest

from pytools._internal.cleanup_paths import cleanup_empty_dirs, cleanup_paths, cleanup_paths_if_content_matches


class TestCleanupPaths:
    def test_directory_is_removed(self, tmp_path: Path):
        target = tmp_path / "skills" / "old-skill"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body", encoding="utf-8")

        removed = cleanup_paths(tmp_path, (Path("skills/old-skill"),))

        assert removed == 1
        assert not target.exists()

    def test_file_is_removed(self, tmp_path: Path):
        target = tmp_path / "old.txt"
        target.write_text("body", encoding="utf-8")

        removed = cleanup_paths(tmp_path, (Path("old.txt"),))

        assert removed == 1
        assert not target.exists()

    def test_missing_path_is_noop(self, tmp_path: Path):
        removed = cleanup_paths(tmp_path, (Path("skills/missing"),))
        assert removed == 0

    def test_missing_base_dir_is_noop(self, tmp_path: Path):
        """base_dir 自体が存在しなくても安全。"""
        removed = cleanup_paths(tmp_path / "not_exists", (Path("a"),))
        assert removed == 0

    def test_symlink_outside_base_is_unlinked_without_deleting_target(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "do_not_delete.txt").write_text("important", encoding="utf-8")
        base = tmp_path / "claude"
        base.mkdir()
        link = base / "linked"
        link.symlink_to(outside)

        removed = cleanup_paths(base, (Path("linked"),))

        assert removed == 1
        assert not link.exists()
        assert outside.exists()
        assert (outside / "do_not_delete.txt").exists()

    def test_multiple_paths_counted(self, tmp_path: Path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").write_text("", encoding="utf-8")

        removed = cleanup_paths(tmp_path, (Path("a"), Path("b"), Path("c")))
        assert removed == 2

    def test_os_error_skips_target_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """1件の検査失敗後も後続対象を削除する。"""
        caplog.set_level("INFO")
        failing = tmp_path / "failing"
        succeeding = tmp_path / "succeeding"
        failing.write_text("保持", encoding="utf-8")
        succeeding.write_text("削除", encoding="utf-8")
        real_exists = Path.exists

        def exists(path: Path) -> bool:
            if path == failing:
                raise OSError("検査失敗")
            return real_exists(path)

        monkeypatch.setattr(Path, "exists", exists)
        with caplog.at_level(logging.INFO):
            removed = cleanup_paths(tmp_path, (Path("failing"), Path("succeeding")))

        assert removed == 1
        assert real_exists(failing)
        assert not real_exists(succeeding)
        assert str(failing) in caplog.text
        assert "検査失敗" in caplog.text

    def test_windows_junction_is_removed_with_rmdir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windowsのジャンクション相当はリンク先を辿らずrmdirで除去する。"""
        target = tmp_path / "skills" / "old-skill"
        target.mkdir(parents=True)
        real_rmdir = Path.rmdir
        removed_with_rmdir: list[Path] = []

        monkeypatch.setattr(Path, "is_junction", lambda path: path == target)

        def record_rmdir(path: Path) -> None:
            removed_with_rmdir.append(path)
            real_rmdir(path)

        monkeypatch.setattr(Path, "rmdir", record_rmdir)

        removed = cleanup_paths(tmp_path, (Path("skills/old-skill"),))

        assert removed == 1
        assert removed_with_rmdir == [target]
        assert not target.exists()

    def test_broken_windows_junction_is_not_treated_as_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "skills" / "old-skill"
        target.mkdir(parents=True)
        real_exists = Path.exists
        real_rmdir = Path.rmdir
        removed_with_rmdir: list[Path] = []

        monkeypatch.setattr(Path, "exists", lambda path: False if path == target else real_exists(path))
        monkeypatch.setattr(Path, "is_junction", lambda path: path == target)

        def record_rmdir(path: Path) -> None:
            removed_with_rmdir.append(path)
            real_rmdir(path)

        monkeypatch.setattr(Path, "rmdir", record_rmdir)

        removed = cleanup_paths(tmp_path, (Path("skills/old-skill"),))

        assert removed == 1
        assert removed_with_rmdir == [target]
        assert not real_exists(target)


class TestCleanupPathsIfContentMatches:
    def test_matching_file_is_removed(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        content = b"# \xe3\x82\xab\xe3\x82\xb9\xe3\x82\xbf\xe3\x83\xa0\n"
        target.write_bytes(content)

        removed = cleanup_paths_if_content_matches(tmp_path, {Path("CLAUDE.md"): content})

        assert removed == 1
        assert not target.exists()

    def test_modified_file_is_kept(self, tmp_path: Path):
        target = tmp_path / "CLAUDE.md"
        target.write_bytes(b"user edited\n")

        removed = cleanup_paths_if_content_matches(tmp_path, {Path("CLAUDE.md"): b"original\n"})

        assert removed == 0
        assert target.exists()
        assert target.read_bytes() == b"user edited\n"

    def test_missing_path_is_noop(self, tmp_path: Path):
        removed = cleanup_paths_if_content_matches(tmp_path, {Path("missing.md"): b"x"})
        assert removed == 0

    def test_missing_base_dir_is_noop(self, tmp_path: Path):
        """base_dir 自体が存在しなくても安全。"""
        removed = cleanup_paths_if_content_matches(tmp_path / "not_exists", {Path("a"): b"x"})
        assert removed == 0

    def test_symlink_outside_base_is_skipped(self, tmp_path: Path):
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

    def test_os_error_skips_target_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """内容一致検査の失敗後も後続対象を削除する。"""
        caplog.set_level("INFO")
        failing = tmp_path / "failing.md"
        succeeding = tmp_path / "succeeding.md"
        failing.write_bytes(b"keep\n")
        succeeding.write_bytes(b"remove\n")
        real_exists = Path.exists

        def exists(path: Path) -> bool:
            if path == failing:
                raise OSError("検査失敗")
            return real_exists(path)

        monkeypatch.setattr(Path, "exists", exists)
        with caplog.at_level(logging.INFO):
            removed = cleanup_paths_if_content_matches(
                tmp_path,
                {Path("failing.md"): b"keep\n", Path("succeeding.md"): b"remove\n"},
            )

        assert removed == 1
        assert real_exists(failing)
        assert not real_exists(succeeding)
        assert str(failing) in caplog.text
        assert "検査失敗" in caplog.text


def test_cleanup_if_content_matches_accepts_sha256(tmp_path: Path) -> None:
    content = b"# old\n" * 1000
    digest = hashlib.sha256(content).hexdigest()
    (tmp_path / "match").write_bytes(content)
    (tmp_path / "edited").write_bytes(content + b"# user\n")
    (tmp_path / "bytes").write_bytes(b"old\n")

    removed = cleanup_paths_if_content_matches(
        tmp_path,
        {Path("match"): digest, Path("edited"): digest, Path("bytes"): b"old\n"},
    )

    assert removed == 2
    assert not (tmp_path / "match").exists()
    assert (tmp_path / "edited").read_bytes() == content + b"# user\n"
    assert not (tmp_path / "bytes").exists()


class TestCleanupEmptyDirs:
    def test_removes_empty_child_then_parent(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b").mkdir(parents=True)

        removed = cleanup_empty_dirs(tmp_path, [Path("a/b"), Path("a")])

        assert removed == 2
        assert not (tmp_path / "a").exists()

    def test_keeps_non_empty_dir(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "keep.txt").write_text("x\n", encoding="utf-8")

        removed = cleanup_empty_dirs(tmp_path, [Path("a/b"), Path("a")])

        assert removed == 1
        assert (tmp_path / "a" / "keep.txt").is_file()

    def test_skips_link_and_outside(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        outside = tmp_path / "outside" / "empty"
        outside.mkdir(parents=True)
        base.mkdir()
        (base / "link").symlink_to(tmp_path / "outside", target_is_directory=True)

        removed = cleanup_empty_dirs(base, [Path("link/empty"), Path("link")])

        assert removed == 0
        assert outside.is_dir()
        assert (base / "link").is_symlink()

    def test_missing_base_dir_is_noop(self, tmp_path: Path) -> None:
        assert cleanup_empty_dirs(tmp_path / "missing", [Path("a")]) == 0
