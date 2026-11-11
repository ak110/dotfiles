"""repack_archiveの基本動作の試験。"""

# pylint: disable=protected-access

import pathlib
import sys
import zipfile

import pytest

from pytools import repack_archive
from pytools._internal.repack_archive_test_helpers import _make_tar, _make_zip, _run_main, _zip_entries


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
        """入力と異なるパスの出力 ZIP が既存なら拒否する (ディレクトリ入力)。"""
        src = tmp_path / "book"
        src.mkdir()
        (src / "01.txt").write_bytes(b"1")
        (tmp_path / "book.zip").write_bytes(b"existing")
        monkeypatch.setattr(sys, "argv", ["repack-archive", "--no-trash", str(src)])
        with pytest.raises(FileExistsError):
            repack_archive.main()

    def test_same_path_output_not_rejected(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """入力と出力が同一パスのアーカイブ入力は既存出力チェックで誤拒否しない。"""
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"a.txt": b"a"})
        # 出力 foo.zip は入力自身であり拒否されない (exit 0 で正常完了)
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
        # `keep/` だけが残り、単一ルートが平坦化されて `a.txt` のみになる
        assert entries == {"a.txt"}

    def test_ignore_dir_only_archive_promotes_contents_before_removal(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """無視対象ディレクトリだけを含む入力でも、初回平坦化後の内容を残す。"""
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
        """libarchive経路でもignore_dirs除去後に残存階層を平坦化する。"""
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
        """libarchive経路でも単独の無視対象ラッパー配下をルートへ昇格する。"""
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
        """連続した単一ディレクトリチェーンを最深まで除去する。"""
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
        """ignore_files で空になった兄弟ディレクトリが平坦化を阻害しないこと。"""
        archive = tmp_path / "foo.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 明示ディレクトリエントリー (末尾 "/" 付き空書き込み)
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
        """同名親子 (foo/foo/bar.txt) でも平坦化が破綻しないこと。"""
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
        """rename_rules は出力 ZIP の stem (アーカイブ名) に適用される。"""
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
        # バックアップは元の名前のまま
        assert (tmp_path / "bk" / "foo.zip").exists()

    def test_rename_rules_do_not_apply_to_inner_entries(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rename_rules はアーカイブ内ファイル名・ディレクトリ名には適用されない。"""
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
        # アーカイブ stem "foo" は rule にマッチしないので出力名は変わらない
        entries = _zip_entries(tmp_path / "foo.zip")
        # 内部のファイル名・ディレクトリ名はそのまま保持される
        assert entries == {"img_dir/img_01.txt", "img_dir/img_02.txt", "other.txt"}

    def test_natsort_order_in_output_zip(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """最終 ZIP のエントリ順は natsort 順 (自然順) に揃う。"""
        archive = tmp_path / "foo.zip"
        # 辞書順だと "001 -> 002 -> 010 -> 020 -> 100" だが、
        # 入力順を入れ替えても natsort で同じ並びになることを検証する
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
        # 元ディレクトリはバックアップへ退避され、入力位置から削除される
        assert (tmp_path / "bk" / "book").exists()
        assert not src.exists()

    def test_directory_ignore_dirs_are_removed_between_flattening_passes(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ディレクトリ入力でもignore_dirs除去後に残存階層を平坦化する。"""
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
        """ディレクトリ入力でも単独の無視対象ラッパー配下をルートへ昇格する。"""
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
