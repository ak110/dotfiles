"""repack_archive モジュールの統合テスト。

実ZIP を合成し、選択的解凍・リネーム・平坦化・ゴミ箱スキップの動作を検証する。
画像変換は tests の副作用を最小にするため画像を含めず、主に配置・フィルタ検証に
フォーカスする (imageconverter 単体は別テストで検証)。
"""

# プライベート関数 (`_preflight_check` など) を直接呼び出して
# 個別シナリオを検証するため、ファイル全体で protected-access を許可する。
# pylint: disable=protected-access,missing-class-docstring

import pathlib
import zipfile

import pytest

from pytools import repack_archive


def _make_zip(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _zip_entries(path: pathlib.Path) -> set[str]:
    with zipfile.ZipFile(path, "r") as zf:
        return set(zf.namelist())


class TestPreflight:
    def test_output_collision_detected(self, tmp_path: pathlib.Path) -> None:
        a = tmp_path / "foo.zip"
        b = tmp_path / "foo.cbz"
        _make_zip(a, {"a.txt": b"a"})
        _make_zip(b, {"b.txt": b"b"})
        with pytest.raises(ValueError, match="出力 ZIP が衝突"):
            repack_archive._preflight_check([a, b], backup_dir=None)

    def test_backup_exists_error(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"a.txt": b"a"})
        (tmp_path / "bk").mkdir()
        (tmp_path / "bk" / "foo.zip").write_bytes(b"existing")
        with pytest.raises(FileExistsError):
            repack_archive._preflight_check([archive], backup_dir=None)


class TestProcessArchive:
    def test_basic_repack(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "sample.zip"
        _make_zip(
            archive,
            {
                "img1.txt": b"one",
                "img2.txt": b"two",
            },
        )
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        output = tmp_path / "sample.zip"
        assert output.exists()
        entries = _zip_entries(output)
        assert entries == {"img1.txt", "img2.txt"}
        # バックアップが残っている (no_trash=True)
        assert (tmp_path / "bk" / "sample.zip").exists()

    def test_ignore_files_skips_extraction(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "keep.txt": b"k",
                "drop.pdf": b"p",
                "Thumbs.db": b"d",
            },
        )
        config = repack_archive.RepackConfig(ignore_files=["*.pdf", "Thumbs.db"])
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"keep.txt"}

    def test_ignore_dirs_skips_extraction(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "keep/a.txt": b"a",
                "NoText/b.txt": b"b",
                "PDF/c.txt": b"c",
            },
        )
        config = repack_archive.RepackConfig(ignore_dirs=["No[-_ ]?Te?xt", "^PDF$"])
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        # `keep/` だけが残り、単一ルートが平坦化されて `a.txt` のみになる
        assert entries == {"a.txt"}

    def test_flatten_single_root(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "sub/01.txt": b"1",
                "sub/02.txt": b"2",
            },
        )
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        # sub/ が平坦化される
        assert entries == {"01.txt", "02.txt"}

    def test_flatten_nested_single_root(self, tmp_path: pathlib.Path) -> None:
        """連続した単一ディレクトリチェーンを最深まで剥がす。"""
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "a/b/c/01.txt": b"1",
                "a/b/c/02.txt": b"2",
            },
        )
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"01.txt", "02.txt"}

    def test_flatten_empty_sibling_after_ignore(self, tmp_path: pathlib.Path) -> None:
        """ignore_files で空になった兄弟ディレクトリが平坦化を阻害しないこと。"""
        archive = tmp_path / "foo.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 明示ディレクトリエントリー (末尾 "/" 付き空書き込み)
            zf.writestr("Series/", b"")
            zf.writestr("Series/Vol01/", b"")
            zf.writestr("Series/Vol02/", b"")
            zf.writestr("Series/Vol01/001.txt", b"content")
            zf.writestr("Series/Vol02/Thumbs.db", b"junk")
        config = repack_archive.RepackConfig(ignore_files=["Thumbs.db"])
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"001.txt"}

    def test_flatten_name_collision(self, tmp_path: pathlib.Path) -> None:
        """同名親子 (foo/foo/bar.txt) でも平坦化が破綻しないこと。"""
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "foo/foo/bar.txt": b"data",
            },
        )
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"bar.txt"}

    def test_multiple_roots_preserved(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "a/01.txt": b"1",
                "b/02.txt": b"2",
            },
        )
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"a/01.txt", "b/02.txt"}

    def test_rename_rules_from_pattern_file(self, tmp_path: pathlib.Path) -> None:
        pattern_file = tmp_path / "rules.txt"
        pattern_file.write_text("^img_\t\n", encoding="utf-8")
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "img_01.txt": b"1",
                "img_02.txt": b"2",
            },
        )
        config = repack_archive.RepackConfig(
            rename_rules=[repack_archive._PatternFileRule(pattern_file="rules.txt")],
        )
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"01.txt", "02.txt"}


class TestUncompressedZip:
    def test_output_is_uncompressed(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"x.txt": b"hello world " * 100})
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        with zipfile.ZipFile(tmp_path / "foo.zip") as zf:
            info = zf.getinfo("x.txt")
            assert info.compress_type == zipfile.ZIP_STORED


class TestProcessDirectory:
    def test_directory_input(self, tmp_path: pathlib.Path) -> None:
        src = tmp_path / "book"
        src.mkdir()
        (src / "01.txt").write_bytes(b"1")
        (src / "02.txt").write_bytes(b"2")
        (src / "unused.pdf").write_bytes(b"pdf")
        config = repack_archive.RepackConfig(ignore_files=["*.pdf"])
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            src,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=True,
            dry_run=False,
        )
        assert (tmp_path / "book.zip").exists()
        entries = _zip_entries(tmp_path / "book.zip")
        assert entries == {"01.txt", "02.txt"}
        # 元ディレクトリはバックアップされたうえで作業ディレクトリが掃除されている
        assert (tmp_path / "bk" / "book").exists()
