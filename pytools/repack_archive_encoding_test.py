# pylint: disable=protected-access

import pathlib
import zipfile

import pytest

from pytools._internal.repack_archive_test_helpers import (
    _Cp932ZipInfo,
    _make_cp932_zip,
    _make_tar,
    _make_zip,
    _RawBytesZipInfo,
    _run_main,
    _zip_entries,
)


class TestBrokenZip:
    @pytest.mark.parametrize("truncate_bytes", [22, 23])
    def test_truncated_zip_raises(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, truncate_bytes: int) -> None:
        """EOCD を含む末尾 ``truncate_bytes`` バイトを切り詰めた ZIP は失敗扱いで副作用なく停止する。

        EOCD の最小サイズは 22 バイト。22 バイトちょうど切り詰めれば EOCD のシグネチャが
        消えて ``zipfile.is_zipfile`` が False を返す。先頭は ``PK\\x03\\x04`` のまま残るため、
        破損 ZIP 検出が境界値の両方で機能していることを担保する。

        ``main()`` 経由では例外が failed_targets に集約されて exit 1 で終了する。
        バックアップ作成前の早期検出のため副作用 (バックアップ・出力 ZIP 上書き) は起きない。
        """
        archive = tmp_path / "truncated.zip"
        _make_zip(archive, {"img1.txt": b"one", "img2.txt": b"two"})
        original = archive.read_bytes()
        assert len(original) > truncate_bytes
        archive.write_bytes(original[:-truncate_bytes])

        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 1

        assert archive.exists()
        assert not (tmp_path / "bk").exists()

        assert archive.stat().st_size == len(original) - truncate_bytes

    def test_non_pk_prefix_is_not_rejected(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "garbage.zip"
        target.write_bytes(b"not a zip file at all")

        exit_code = _run_main(monkeypatch, ["--no-trash", str(target)])
        assert exit_code == 1


class TestFilenameEncoding:
    def test_cp932_zip_decodes_correctly(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "ja.zip"
        _make_cp932_zip(
            archive,
            {
                "あいう.txt": b"a",
                "フォルダ/項目.txt": b"b",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "ja.zip")

        assert entries == {"あいう.txt", "フォルダ/項目.txt"}

    def test_mixed_ascii_and_utf8_zip(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ASCII (bit 11 未設定) と UTF-8 日本語名 (bit 11 設定) が混在しても両方破損しない。

        アーカイブ全体を単一エンコーディングと誤判定すると、UTF-8 バイト列が CP932 として
        再解釈されて非 ASCII 名が破損する。エントリ単位の bit 11 参照で回避できていることを検証する。
        """
        archive = tmp_path / "mixed.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("README.txt", b"readme")
            # 日本語名: zipfile へフラグを指定しない書き込みで UTF-8 + bit 11 設定
            zf.writestr("日本語.txt", b"ja")
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "mixed.zip")
        assert entries == {"README.txt", "日本語.txt"}

    def test_sjis_backslash_byte_is_decoded_as_single_path_component(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = tmp_path / "sjis.zip"
        _make_cp932_zip(
            archive,
            {
                "ソート/メモ.txt": b"x",
                "他.txt": b"y",
            },
        )
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "sjis.zip")
        assert entries == {"ソート/メモ.txt", "他.txt"}

    def test_cp932_and_utf8_mixed_zip(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """bit 11 未設定の CP932 日本語エントリと bit 11 付き UTF-8 日本語エントリの混在で双方破損しない。

        CP932 の 2 バイト目に ``0x5C`` を含む文字 (ソ) を CP932 側に格納して、
        ``info.orig_filename`` 経由でデコードできていることを担保する。
        """
        archive = tmp_path / "mixed.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(_Cp932ZipInfo("ソート.txt"), b"a")
            zf.writestr("日本語.txt", b"u")
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "mixed.zip")
        assert entries == {"ソート.txt", "日本語.txt"}

    def test_null_byte_entry_is_truncated(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """エントリ名に NUL 文字が混入した場合は NUL 以降を切り詰めて展開する。

        ``pytilpack.zipfile.decode_zipinfo_filename`` が NUL 以降を切り詰める仕様のため、
        ``bad\\x00name.txt`` は ``bad`` という名前で展開され、他エントリと並んで成功扱いになる。
        出力 ZIP に ``bad`` エントリが含まれていることで振る舞いを確認する。
        """
        archive = tmp_path / "nul.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(_RawBytesZipInfo(b"bad\x00name.txt"), b"x")
            zf.writestr("safe.txt", b"y")
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "nul.zip")
        assert "bad" in entries
        assert "safe.txt" in entries

    def test_cp932_decode_failure_falls_back_per_entry(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CP932 strict でデコードできないエントリだけ CP437 にフォールバックする。

        他の CP932 エントリは正しく日本語名で展開される。アーカイブ単位の一括判定では
        1 件の失敗で全体が CP437 に転落するため、エントリ単位フォールバックが
        機能していることをここで担保する。
        """
        archive = tmp_path / "fallback.zip"
        # 0x81 は CP932 の有効な lead byte だが 0x39 ('9') は有効な trail byte 範囲外なため
        # strict デコードで必ず失敗する。CP437 ではそれぞれ ``ü`` ``9`` としてデコードされる。
        raw_invalid = b"\x81\x39hi.txt"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(_Cp932ZipInfo("和文.txt"), b"a")
            zf.writestr(_RawBytesZipInfo(raw_invalid), b"b")

            zf.writestr("safe.txt", b"c")
        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 0
        entries = _zip_entries(tmp_path / "fallback.zip")
        cp437_fallback = raw_invalid.decode("cp437")
        assert entries == {"和文.txt", cp437_fallback, "safe.txt"}


class TestExtractZipPathSafety:
    """ZIP のエントリ名による不正パスを失敗扱いとする挙動の検証。"""

    def test_unsafe_paths_are_recorded_as_failures(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = tmp_path / "unsafe.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("../escape.txt", b"e")
            zf.writestr("/abs.txt", b"a")
            zf.writestr("C:/win.txt", b"w")
            # PureWindowsPath で root 持ち判定になるバックスラッシュ前置きも拒否
            zf.writestr(_RawBytesZipInfo(b"\\back.txt"), b"b")
            zf.writestr("safe.txt", b"s")

        exit_code = _run_main(monkeypatch, ["--no-trash", str(archive)])
        assert exit_code == 1

        # 安全なエントリは出力 ZIP に含まれる
        entries = _zip_entries(tmp_path / "unsafe.zip")
        assert "safe.txt" in entries

        assert "../escape.txt" not in entries
        assert "/abs.txt" not in entries
        assert "C:/win.txt" not in entries

        assert not (tmp_path / "escape.txt").exists()


class TestExtractLibarchivePathSafety:
    """libarchiveのエントリ名による不正パスを失敗扱いとする挙動の検証。"""

    @pytest.mark.parametrize(
        ("entry_path", "config_text"),
        [
            ("../escaped.txt", None),
            ("NoText/../../escaped.txt", "ignore_dirs: ['NoText']\n"),
        ],
    )
    def test_unsafe_paths_are_recorded_before_extraction(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        entry_path: str,
        config_text: str | None,
    ) -> None:
        archive = tmp_path / "unsafe.tar"
        _make_tar(archive, {entry_path: b"escaped", "safe.txt": b"safe"})
        args = ["--no-trash"]
        if config_text is not None:
            config_yaml = tmp_path / "repack-archive.yaml"
            config_yaml.write_text(config_text, encoding="utf-8")
            args.extend(["--config", str(config_yaml)])
        args.append(str(archive))

        exit_code = _run_main(monkeypatch, args)

        assert exit_code == 1
        assert not (tmp_path / "escaped.txt").exists()
        assert _zip_entries(tmp_path / "unsafe.zip") == {"safe.txt"}
