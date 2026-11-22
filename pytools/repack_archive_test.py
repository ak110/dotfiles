# pylint: disable=protected-access

import pathlib
import sys
import zipfile

import pytest

from pytools import repack_archive
from pytools._internal.repack_archive_test_helpers import _make_tar, _make_zip, _run_main, _zip_entries


class TestConditionalDirectoryExclusion:
    @pytest.mark.parametrize("source_kind", ["zip", "tar", "directory"])
    def test_sibling_conditions_apply_before_flattening(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, source_kind: str
    ) -> None:
        files = {
            "cover.txt": b"cover",
            "series/one/JpG/drop.txt": b"drop",
            "series/one/PNG/keep.txt": b"keep",
            "series/two/jpg/keep.txt": b"keep",
            "series/three/png": b"file",
            "series/three/jpg/keep.txt": b"keep",
            "raw/drop.txt": b"drop",
            "tiff/keep.txt": b"keep",
            "trash/drop.txt": b"drop",
        }
        source = tmp_path / ("book" if source_kind == "directory" else f"book.{source_kind}")
        if source_kind == "directory":
            for name, content in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        elif source_kind == "tar":
            _make_tar(source, files)
        else:
            _make_zip(source, files)
        config = tmp_path / "rules.yaml"
        config.write_text(
            "ignore_dirs:\n"
            "  - '^trash$'\n"
            "  - pattern: '^jp(g|eg)$'\n"
            "    if_sibling: '^png$'\n"
            "  - pattern: '^raw$'\n"
            "    if_sibling: '^tiff$'\n",
            encoding="utf-8",
        )

        assert _run_main(monkeypatch, ["--no-trash", "--config", str(config), str(source)]) == 0
        assert _zip_entries(tmp_path / "book.zip") == {
            "cover.txt",
            "series/one/PNG/keep.txt",
            "series/two/jpg/keep.txt",
            "series/three/png",
            "series/three/jpg/keep.txt",
            "tiff/keep.txt",
        }

    def test_mutual_conditions_use_original_tree_and_restore_empty_result(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = tmp_path / "book.zip"
        _make_zip(source, {"jpg/a.txt": b"a", "png/b.txt": b"b"})
        original = source.read_bytes()
        config = tmp_path / "rules.yaml"
        config.write_text(
            "ignore_dirs:\n  - pattern: '^jpg$'\n    if_sibling: '^png$'\n  - pattern: '^png$'\n    if_sibling: '^jpg$'\n",
            encoding="utf-8",
        )
        assert _run_main(monkeypatch, ["--no-trash", "--config", str(config), str(source)]) == 1
        assert source.read_bytes() == original

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_directory_does_not_count_itself_as_sibling(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
    ) -> None:
        source = tmp_path / "book.zip"
        _make_zip(source, {"jpg/keep.txt": b"keep"})
        original = source.read_bytes()
        config = tmp_path / "rules.yaml"
        config.write_text("ignore_dirs: [{pattern: '^jpg$', if_sibling: '^jpg$'}]\n", encoding="utf-8")
        arguments = ["--no-trash", "--config", str(config), str(source)]
        if dry_run:
            arguments.append("--dry-run")
        assert _run_main(monkeypatch, arguments) == 0
        if dry_run:
            assert source.read_bytes() == original
        else:
            assert _zip_entries(source) == {"keep.txt"}


class TestPreflight:
    def test_output_collision_detected(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        a = tmp_path / "foo.zip"
        b = tmp_path / "foo.cbz"
        _make_zip(a, {"a.txt": b"a"})
        _make_zip(b, {"b.txt": b"b"})
        # _preflight_check は副作用ゼロの事前検証フェーズとして設計されており、
        # ValueError を伝播させて即座に中断する（_process_target が try/except で各件を
        # キャッチして継続するのとは対照的な設計）。SystemExit(1) に統一しない理由は、
        # preflight 失敗は「処理開始前の中断」であり CLI を使うエンドユーザーには未捕捉例外として
        # スタックトレースを見せることが意図されているため。
        monkeypatch.setattr(sys, "argv", ["repack-archive", "--no-trash", str(a), str(b)])
        with pytest.raises(ValueError, match="出力 ZIP が衝突"):
            repack_archive.main()

    def test_backup_exists_error(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"a.txt": b"a"})
        (tmp_path / "bk").mkdir()
        (tmp_path / "bk" / "foo.zip").write_bytes(b"existing")
        monkeypatch.setattr(sys, "argv", ["repack-archive", "--no-trash", str(archive)])
        with pytest.raises(FileExistsError):
            repack_archive.main()

    def test_existing_output_rejected_when_path_differs(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "book"
        src.mkdir()
        (src / "01.txt").write_bytes(b"1")
        (tmp_path / "book.zip").write_bytes(b"existing")
        monkeypatch.setattr(sys, "argv", ["repack-archive", "--no-trash", str(src)])
        with pytest.raises(FileExistsError):
            repack_archive.main()

    def test_same_path_output_not_rejected(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"a.txt": b"a"})

        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0


class TestProcessArchive:
    def test_basic_repack(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "sample.zip"
        _make_zip(
            archive,
            {
                "img1.txt": b"one",
                "img2.txt": b"two",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        output = tmp_path / "sample.zip"
        assert output.exists()
        entries = _zip_entries(output)
        assert entries == {"img1.txt", "img2.txt"}
        assert (tmp_path / "bk" / "sample.zip").exists()

    def test_ignore_files_skips_extraction(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "keep.txt": b"k",
                "drop.pdf": b"p",
                "Thumbs.db": b"d",
            },
        )
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_files: ['*.pdf', 'Thumbs.db']\n", encoding="utf-8")
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(archive)],
        )
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"keep.txt"}

    def test_ignore_dirs_are_removed_after_flattening(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "keep/a.txt": b"a",
                "NoText/b.txt": b"b",
                "PDF/c.txt": b"c",
            },
        )
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt', '^PDF$']\n", encoding="utf-8")
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(archive)],
        )
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")

        assert entries == {"a.txt"}

    def test_ignore_dir_only_archive_promotes_contents_before_removal(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"NoText/b.txt": b"b"})
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")

        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(archive)])

        assert exit_code == 0
        assert _zip_entries(tmp_path / "foo.zip") == {"b.txt"}

    def test_ignore_dirs_are_removed_after_flattening_for_libarchive(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = tmp_path / "foo.tar"
        _make_tar(
            archive,
            {
                "keep/a.txt": b"a",
                "NoText/b.txt": b"b",
            },
        )
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")

        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(archive)])

        assert exit_code == 0
        assert _zip_entries(tmp_path / "foo.zip") == {"a.txt"}

    def test_ignore_dir_only_archive_promotes_contents_for_libarchive(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = tmp_path / "foo.tar"
        _make_tar(archive, {"NoText/b.txt": b"b"})
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")

        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(archive)])

        assert exit_code == 0
        assert _zip_entries(tmp_path / "foo.zip") == {"b.txt"}

    def test_flatten_single_root(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "sub/01.txt": b"1",
                "sub/02.txt": b"2",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"01.txt", "02.txt"}

    def test_flatten_nested_single_root(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "a/b/c/01.txt": b"1",
                "a/b/c/02.txt": b"2",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"01.txt", "02.txt"}

    def test_flatten_empty_sibling_after_ignore(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Series/", b"")
            zf.writestr("Series/Vol01/", b"")
            zf.writestr("Series/Vol02/", b"")
            zf.writestr("Series/Vol01/001.txt", b"content")
            zf.writestr("Series/Vol02/Thumbs.db", b"junk")
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_files: ['Thumbs.db']\n", encoding="utf-8")
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(archive)],
        )
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"001.txt"}

    def test_flatten_name_collision(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "foo/foo/bar.txt": b"data",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"bar.txt"}

    def test_multiple_roots_preserved(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "a/01.txt": b"1",
                "b/02.txt": b"2",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "foo.zip")
        assert entries == {"a/01.txt", "b/02.txt"}

    def test_rename_rules_apply_to_archive_stem(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pattern_file = tmp_path / "rules.txt"
        pattern_file.write_text("^foo$\tbar\n", encoding="utf-8")
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "img_01.txt": b"1",
                "img_02.txt": b"2",
            },
        )
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("rename_rules:\n  - pattern_file: rules.txt\n", encoding="utf-8")
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(archive)],
        )
        assert exit_code == 0
        # 出力 ZIP は rename 適用後の stem
        assert (tmp_path / "bar.zip").exists()
        assert not (tmp_path / "foo.zip").exists()

        assert (tmp_path / "bk" / "foo.zip").exists()

    def test_rename_rules_do_not_apply_to_inner_entries(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pattern_file = tmp_path / "rules.txt"
        pattern_file.write_text("^img_\t\n", encoding="utf-8")
        archive = tmp_path / "foo.zip"
        _make_zip(
            archive,
            {
                "img_dir/img_01.txt": b"1",
                "img_dir/img_02.txt": b"2",
                "other.txt": b"3",
            },
        )
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("rename_rules:\n  - pattern_file: rules.txt\n", encoding="utf-8")
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(archive)],
        )
        assert exit_code == 0

        entries = _zip_entries(tmp_path / "foo.zip")

        assert entries == {"img_dir/img_01.txt", "img_dir/img_02.txt", "other.txt"}

    def test_natsort_order_in_output_zip(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"

        _make_zip(
            archive,
            {
                "100.txt": b"hundred",
                "010.txt": b"ten",
                "001.txt": b"one",
                "020.txt": b"twenty",
                "002.txt": b"two",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        with zipfile.ZipFile(tmp_path / "foo.zip") as zf:
            ordered = [info.filename for info in zf.infolist()]
        assert ordered == ["001.txt", "002.txt", "010.txt", "020.txt", "100.txt"]


class TestUncompressedZip:
    def test_output_is_uncompressed(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"x.txt": b"hello world " * 100})
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        with zipfile.ZipFile(tmp_path / "foo.zip") as zf:
            info = zf.getinfo("x.txt")
            assert info.compress_type == zipfile.ZIP_STORED


class TestProcessDirectory:
    def test_directory_input(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "book"
        src.mkdir()
        (src / "01.txt").write_bytes(b"1")
        (src / "02.txt").write_bytes(b"2")
        (src / "unused.pdf").write_bytes(b"pdf")
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_files: ['*.pdf']\n", encoding="utf-8")
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(src)],
        )
        assert exit_code == 0
        assert (tmp_path / "book.zip").exists()
        entries = _zip_entries(tmp_path / "book.zip")
        assert entries == {"01.txt", "02.txt"}

        assert (tmp_path / "bk" / "book").exists()
        assert not src.exists()

    def test_directory_ignore_dirs_are_removed_between_flattening_passes(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src = tmp_path / "book"
        (src / "keep").mkdir(parents=True)
        (src / "NoText").mkdir()
        (src / "keep" / "01.txt").write_bytes(b"1")
        (src / "NoText" / "drop.txt").write_bytes(b"drop")
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")

        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(src)])

        assert exit_code == 0
        assert _zip_entries(tmp_path / "book.zip") == {"01.txt"}

    def test_directory_ignore_dir_only_promotes_contents(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src = tmp_path / "book"
        (src / "NoText").mkdir(parents=True)
        (src / "NoText" / "01.txt").write_bytes(b"1")
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")

        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(src)])

        assert exit_code == 0
        assert _zip_entries(tmp_path / "book.zip") == {"01.txt"}

    def test_directory_input_send2trash(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """フォルダ入力でも警告・失敗が無ければバックアップが send2trash に渡る。"""
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        src = tmp_path / "book"
        src.mkdir()
        (src / "01.txt").write_bytes(b"1")
        (src / "02.txt").write_bytes(b"2")
        exit_code = _run_main(monkeypatch, [str(src)])
        assert exit_code == 0
        assert len(trashed) == 1
        assert trashed[0].endswith("book")
