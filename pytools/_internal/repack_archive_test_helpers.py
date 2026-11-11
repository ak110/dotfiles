"""repack_archive試験の共通fixture・入力と補助関数。"""

import io
import pathlib
import struct
import sys
import tarfile
import zipfile

import PIL.Image
import pytest

from pytools import repack_archive


def _make_zip(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _make_pdf(path: pathlib.Path, pages: list[tuple[tuple[int, int], str]]) -> None:
    """指定サイズ・色の各ページから複数ページ PDF を生成する。"""
    images = [PIL.Image.new("RGB", size, color=color) for size, color in pages]
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])


class _Cp932ZipInfo(zipfile.ZipInfo):
    """ファイル名を CP932 でエンコードし、Unicode flag (bit 11) を未設定にする ZipInfo。

    Python 標準の ``ZipInfo._encodeFilenameFlags`` は非 ASCII 名を UTF-8 に変換して
    bit 11 をセットする。これを上書きすることで、libarchive が過去の日本語 ZIP として
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

    CP932 strict でデコードできないバイト列を含むエントリをテストで合成するために使う。
    bit 11 はセットしない。
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


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> int:
    """``repack_archive.main()`` を指定引数で実行し、終了コードを返す。"""
    monkeypatch.setattr(sys, "argv", ["repack-archive", *args])
    with pytest.raises(SystemExit) as exc_info:
        repack_archive.main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code


def _png_bytes(size: tuple[int, int] = (50, 50), color: tuple[int, int, int] = (0, 255, 0)) -> bytes:
    buf = io.BytesIO()
    PIL.Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _png_bytes_with_corrupt_text() -> bytes:
    """TEXt チャンクの CRC を意図的に破損させた PNG バイト列を返す。

    Pillow 通常モード（LOAD_TRUNCATED_IMAGES = False）では UnidentifiedImageError が発生するが、寛容モードでは
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


def _make_tar(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _snapshot(target: pathlib.Path) -> object:
    """ファイルはバイト列、ディレクトリは相対パスと内容の組で原本を比較できる形にする。"""
    if target.is_file():
        return target.read_bytes()
    return sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file())


def _make_input(tmp_path: pathlib.Path, kind: str) -> pathlib.Path:
    """入力形態ごとに、作業領域の準備前に退避される原本を作成する。"""
    if kind == "zip":
        target = tmp_path / "book.zip"
        _make_zip(target, {"page.txt": b"page"})
    elif kind == "directory":
        target = tmp_path / "book_dir"
        (target / "inner").mkdir(parents=True)
        (target / "inner" / "page.txt").write_bytes(b"page")
    else:
        target = tmp_path / "book.pdf"
        target.write_bytes(b"%PDF-1.4\n%placeholder\n")
    return target
