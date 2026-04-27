"""repack_archive モジュールの統合テスト。

実ZIP を合成し、選択的解凍・リネーム・平坦化・ゴミ箱スキップの動作を検証する。
画像変換は tests の副作用を最小にするため画像を含めず、主に配置・フィルタ検証に
フォーカスする (imageconverter 単体は別テストで検証)。
"""

# プライベート関数 (`_preflight_check` など) を直接呼び出して
# 個別シナリオを検証するため、ファイル全体で protected-access を許可する。
# pylint: disable=protected-access,missing-class-docstring

import io
import logging
import pathlib
import struct
import zipfile

import PIL.Image
import pytest

from pytools import repack_archive


def _make_zip(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


class _Cp932ZipInfo(zipfile.ZipInfo):
    """ファイル名を CP932 でエンコードし、Unicode flag (bit 11) を立てない ZipInfo。

    Python 標準の ``ZipInfo._encodeFilenameFlags`` は非 ASCII 名を UTF-8 に変換して
    bit 11 を立てる。これを上書きすることで、libarchive が過去の日本語 ZIP として
    誤解釈するパターン (CP932 生バイト + bit 11 未設定) をテストで再現できる。
    """

    def _encodeFilenameFlags(self) -> tuple[bytes, int]:
        return self.filename.encode("cp932"), self.flag_bits & ~0x800


def _make_cp932_zip(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    """CP932 でエンコードされ、bit 11 未設定のエントリのみを含む ZIP を作成する。"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, data in entries.items():
            zf.writestr(_Cp932ZipInfo(name), data)


class _RawBytesZipInfo(zipfile.ZipInfo):
    """ファイル名として任意の生バイト列を書き込む ZipInfo。

    CP932 strict で復号できないバイト列を含むエントリをテストで合成するために使う。
    bit 11 は立てない。
    """

    def __init__(self, raw_name: bytes) -> None:
        # ZipInfo は filename を str で保持する。CP437 経由で str 化しておけば
        # ``_encodeFilenameFlags`` を上書きする側が責任を持って raw_name を返す限り、
        # 実 ZIP ファイル上は raw_name 通りのバイト列が記録される。
        super().__init__(raw_name.decode("cp437"))
        self._raw_name = raw_name

    def _encodeFilenameFlags(self) -> tuple[bytes, int]:
        return self._raw_name, self.flag_bits & ~0x800


def _zip_entries(path: pathlib.Path) -> set[str]:
    with zipfile.ZipFile(path, "r") as zf:
        return set(zf.namelist())


class TestPreflight:
    def test_output_collision_detected(self, tmp_path: pathlib.Path) -> None:
        a = tmp_path / "foo.zip"
        b = tmp_path / "foo.cbz"
        _make_zip(a, {"a.txt": b"a"})
        _make_zip(b, {"b.txt": b"b"})
        compiled = repack_archive._compile_rules(repack_archive.RepackConfig(), tmp_path)
        with pytest.raises(ValueError, match="出力 ZIP が衝突"):
            repack_archive._preflight_check([a, b], compiled=compiled, backup_dir=None)

    def test_backup_exists_error(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "foo.zip"
        _make_zip(archive, {"a.txt": b"a"})
        (tmp_path / "bk").mkdir()
        (tmp_path / "bk" / "foo.zip").write_bytes(b"existing")
        compiled = repack_archive._compile_rules(repack_archive.RepackConfig(), tmp_path)
        with pytest.raises(FileExistsError):
            repack_archive._preflight_check([archive], compiled=compiled, backup_dir=None)


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
        """連続した単一ディレクトリチェーンを最深まで除去する。"""
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

    def test_rename_rules_apply_to_archive_stem(self, tmp_path: pathlib.Path) -> None:
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
        # 出力 ZIP は rename 適用後の stem
        assert (tmp_path / "bar.zip").exists()
        assert not (tmp_path / "foo.zip").exists()
        # バックアップは元の名前のまま
        assert (tmp_path / "bk" / "foo.zip").exists()

    def test_rename_rules_do_not_apply_to_inner_entries(self, tmp_path: pathlib.Path) -> None:
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
        # アーカイブ stem "foo" は rule にマッチしないので出力名は変わらない
        entries = _zip_entries(tmp_path / "foo.zip")
        # 内部のファイル名・ディレクトリ名はそのまま保持される
        assert entries == {"img_dir/img_01.txt", "img_dir/img_02.txt", "other.txt"}

    def test_natsort_order_in_output_zip(self, tmp_path: pathlib.Path) -> None:
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
            ordered = [info.filename for info in zf.infolist()]
        assert ordered == ["001.txt", "002.txt", "010.txt", "020.txt", "100.txt"]


def _png_bytes(size: tuple[int, int] = (50, 50), color: tuple[int, int, int] = (0, 255, 0)) -> bytes:
    buf = io.BytesIO()
    PIL.Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _png_bytes_with_corrupt_text() -> bytes:
    """tEXt チャンクの CRC を意図的に破損させた PNG バイト列を返す。

    Pillow 既定モードでは UnidentifiedImageError が送出されるが、寛容モードでは
    画像本体が読み込まれる。imageconverter の 2 段構え動作確認用。
    """
    data = _png_bytes()
    pos = 8
    while pos < len(data):
        chunk_len = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        if chunk_type == b"IHDR":
            after_ihdr = pos + 8 + chunk_len + 4
            text_data = b"Comment\x00broken"
            corrupt_chunk = struct.pack(">I", len(text_data)) + b"tEXt" + text_data + b"\x00\x00\x00\x00"
            return data[:after_ihdr] + corrupt_chunk + data[after_ihdr:]
        pos += 8 + chunk_len + 4
    raise AssertionError("IHDR が見つからない")


class TestImageConversionBackupRetention:
    """画像変換の警告・失敗で原本バックアップが保持されることの検証。"""

    def test_corrupt_text_png_warning_keeps_backup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """tEXt CRC 不整合 PNG は寛容モードで変換されつつバックアップが保持される。"""
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("img.png", _png_bytes_with_corrupt_text())
            # 単一ルート平坦化に巻き込まれないよう別エントリも置く
            zf.writestr("readme.txt", b"hello")
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=False,
            dry_run=False,
        )
        # 出力 ZIP には JPEG が含まれる
        entries = _zip_entries(tmp_path / "sample.zip")
        assert "img.jpg" in entries
        # 警告があったためバックアップは保持され send2trash も呼ばれない
        assert (tmp_path / "bk" / "sample.zip").exists()
        assert not trashed

    def test_completely_broken_image_error_keeps_backup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """完全に壊れた PNG は変換失敗となるが、原本バックアップは保持される。"""
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("img.png", b"not a PNG file at all")
            zf.writestr("readme.txt", b"hello")
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=False,
            dry_run=False,
        )
        # バックアップは保持される
        assert (tmp_path / "bk" / "sample.zip").exists()
        assert not trashed
        # 出力 ZIP は生成されるが壊れた画像は除外される
        entries = _zip_entries(tmp_path / "sample.zip")
        assert "readme.txt" in entries
        assert "img.png" not in entries

    def test_clean_run_trashes_backup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """警告・失敗が無い場合はバックアップがゴミ箱送りされる (回帰防止)。"""
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("img.png", _png_bytes())
            zf.writestr("readme.txt", b"hello")
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        repack_archive._process_target(
            archive,
            config=config,
            compiled=compiled,
            backup_dir_override=None,
            no_trash=False,
            dry_run=False,
        )
        # 通常の変換成功ではバックアップが send2trash 経由で送られる
        assert len(trashed) == 1
        assert trashed[0].endswith("sample.zip")


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
        # 元ディレクトリはバックアップされたうえで作業ディレクトリが削除されている
        assert (tmp_path / "bk" / "book").exists()


class TestFilenameEncoding:
    """ZIP のファイル名エンコーディング破損を避ける挙動の検証。"""

    def test_cp932_zip_decodes_correctly(self, tmp_path: pathlib.Path) -> None:
        """bit 11 未設定の CP932 ZIP で日本語ファイル名を正しく復元できる。"""
        archive = tmp_path / "ja.zip"
        _make_cp932_zip(
            archive,
            {
                "あいう.txt": b"a",
                "フォルダ/項目.txt": b"b",
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
        entries = _zip_entries(tmp_path / "ja.zip")
        # 単一ルート "フォルダ" は平坦化されないよう、ルートが複数になる構造にしてある
        assert entries == {"あいう.txt", "フォルダ/項目.txt"}

    def test_mixed_ascii_and_utf8_zip(self, tmp_path: pathlib.Path) -> None:
        """ASCII (bit 11 未設定) と UTF-8 日本語名 (bit 11 設定) が混在しても両方破損しない。

        アーカイブ全体を単一エンコーディングと誤判定すると、UTF-8 バイト列が CP932 として
        再解釈されて非 ASCII 名が破損する。エントリ単位の bit 11 参照で回避できていることを検証する。
        """
        archive = tmp_path / "mixed.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            # ASCII 名: zipfile 既定動作で bit 11 未設定
            zf.writestr("README.txt", b"readme")
            # 日本語名: zipfile 既定動作で UTF-8 + bit 11 設定
            zf.writestr("日本語.txt", b"ja")
        # 事前確認: 想定どおりの bit 11 構成になっている
        with zipfile.ZipFile(archive, "r") as zf:
            flags = {info.filename: info.flag_bits & 0x800 for info in zf.infolist()}
        assert flags["README.txt"] == 0
        assert flags["日本語.txt"] == 0x800

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
        entries = _zip_entries(tmp_path / "mixed.zip")
        assert entries == {"README.txt", "日本語.txt"}

    def test_sjis_backslash_byte_is_decoded_as_single_path_component(self, tmp_path: pathlib.Path) -> None:
        """SJIS 2バイト目に 0x5C を含む文字 (例: ソ) でディレクトリ階層が壊れない。

        報告された系統 B の再現。``ソ`` の SJIS バイト列は ``b"\\x83\\x5c"`` で、
        2 バイト目が ASCII のバックスラッシュと同値である。文字列処理を誤ると
        ``\\`` がパス区切りと解釈され、``pathlib.Path`` が意図しないディレクトリ階層に
        分割してしまう。
        """
        archive = tmp_path / "sjis.zip"
        _make_cp932_zip(
            archive,
            {
                # "ソート" は先頭 "ソ" が 0x83 0x5C
                "ソート/メモ.txt": b"x",
                # 単一ルート平坦化を避けるため別ルートも置く
                "他.txt": b"y",
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
        entries = _zip_entries(tmp_path / "sjis.zip")
        assert entries == {"ソート/メモ.txt", "他.txt"}

    def test_cp932_and_utf8_mixed_zip(self, tmp_path: pathlib.Path) -> None:
        """bit 11 未設定の CP932 日本語エントリと bit 11 付き UTF-8 日本語エントリの混在で双方破損しない。

        CP932 の 2 バイト目に ``0x5C`` を含む文字 (ソ) を CP932 側に入れて、
        ``info.filename`` 経由ではなく ``info.orig_filename`` 経由で復号できていることを担保する。
        """
        archive = tmp_path / "mixed.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(_Cp932ZipInfo("ソート.txt"), b"a")
            zf.writestr("日本語.txt", b"u")
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
        entries = _zip_entries(tmp_path / "mixed.zip")
        assert entries == {"ソート.txt", "日本語.txt"}

    def test_null_byte_entry_is_truncated(self, tmp_path: pathlib.Path) -> None:
        """エントリ名に NUL 文字が混入した場合は NUL 以降を切り詰めて展開する。

        ``pytilpack.zipfile.decode_zipinfo_filename`` が NUL 以降を切り詰める仕様のため、
        ``bad\\x00name.txt`` は ``bad`` という名前で展開され、他エントリと並んで成功扱いになる。
        """
        archive = tmp_path / "nul.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(_RawBytesZipInfo(b"bad\x00name.txt"), b"x")
            zf.writestr("safe.txt", b"y")
        dest = tmp_path / "out"
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        failures = repack_archive._extract_zip(archive, dest, compiled)
        assert not failures
        assert (dest / "bad").read_bytes() == b"x"
        assert (dest / "safe.txt").read_bytes() == b"y"

    def test_cp932_decode_failure_falls_back_per_entry(self, tmp_path: pathlib.Path) -> None:
        """CP932 strict で復号できないエントリだけ CP437 にフォールバックする。

        他の CP932 エントリは正しく日本語名で展開される。アーカイブ単位の一括判定では
        1 件の失敗で全体が CP437 に転落するため、エントリ単位フォールバックが
        効いていることをここで担保する。
        """
        archive = tmp_path / "fallback.zip"
        # 0x81 は CP932 の有効な lead byte だが 0x39 ('9') は有効な trail byte 範囲外なため
        # strict 復号で必ず失敗する。CP437 ではそれぞれ ``ü`` ``9`` として復号される。
        raw_invalid = b"\x81\x39hi.txt"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(_Cp932ZipInfo("和文.txt"), b"a")
            zf.writestr(_RawBytesZipInfo(raw_invalid), b"b")
            # 単一ルート平坦化に巻き込まれないよう ASCII エントリも追加
            zf.writestr("safe.txt", b"c")
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
        entries = _zip_entries(tmp_path / "fallback.zip")
        cp437_fallback = raw_invalid.decode("cp437")
        assert entries == {"和文.txt", cp437_fallback, "safe.txt"}


class TestExtractZipPathSafety:
    """ZIP のエントリ名による不正パスを失敗扱いとする挙動の検証。"""

    def test_unsafe_paths_are_recorded_as_failures(self, tmp_path: pathlib.Path) -> None:
        archive = tmp_path / "unsafe.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("../escape.txt", b"e")
            zf.writestr("/abs.txt", b"a")
            zf.writestr("C:/win.txt", b"w")
            # PureWindowsPath で root 持ち判定になるバックスラッシュ前置きも拒否
            zf.writestr(_RawBytesZipInfo(b"\\back.txt"), b"b")
            zf.writestr("safe.txt", b"s")
        dest = tmp_path / "out"
        config = repack_archive.RepackConfig()
        compiled = repack_archive._compile_rules(config, tmp_path)
        failures = repack_archive._extract_zip(archive, dest, compiled)

        failed_paths = {p for p, _, _ in failures}
        assert "../escape.txt" in failed_paths
        assert "/abs.txt" in failed_paths
        assert "C:/win.txt" in failed_paths
        assert "\\back.txt" in failed_paths
        # 安全なエントリだけ書き出されている
        assert (dest / "safe.txt").exists()
        # 親ディレクトリへの脱出は阻止されている
        assert not (tmp_path / "escape.txt").exists()


class TestMainFailureSummary:
    """複数 target の失敗集約と終了コードの検証。"""

    def test_failure_summary_lists_failed_targets(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        good = tmp_path / "good.zip"
        _make_zip(good, {"a.txt": b"a"})
        # ZIP の体をなさないファイル。`_preflight_check` は存在チェックのみ通過し、
        # libarchive がフォーマットエラーで例外を送出する。
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip file at all, totally garbage contents")

        monkeypatch.setattr("sys.argv", ["repack-archive", "--no-trash", str(good), str(bad)])
        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        with pytest.raises(SystemExit) as exc_info:
            repack_archive._main()
        assert exc_info.value.code == 1

        # 良い target は出力 ZIP が残り、失敗 target はサマリーに列挙される
        assert (tmp_path / "good.zip").exists()
        summary = "\n".join(r.getMessage() for r in caplog.records)
        assert "失敗したターゲット" in summary
        assert str(bad) in summary
