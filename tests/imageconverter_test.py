"""imageconverter モジュールの単体テスト。"""

import io
import pathlib
import struct
import unittest.mock
import warnings

import PIL.Image
import PIL.ImageFile
import pytest

from pytools import imageconverter


def _make_png(path: pathlib.Path, size: tuple[int, int] = (100, 100)) -> None:
    PIL.Image.new("RGB", size, color=(255, 0, 0)).save(path, format="PNG")


def _make_corrupt_text_png(path: pathlib.Path) -> None:
    """tEXt チャンクの CRC を意図的に破損させた PNG を生成する。

    Pillow 既定モード（``LOAD_TRUNCATED_IMAGES = False``）では
    ``UnidentifiedImageError`` が送出されるが、寛容モードでは画像本体を
    読み込めるという、`open_image_with_exif` の 2 段構え動作確認用の入力。
    """
    buf = io.BytesIO()
    PIL.Image.new("RGB", (50, 50), color=(0, 255, 0)).save(buf, format="PNG")
    data = buf.getvalue()
    pos = 8  # PNG シグネチャの後
    while pos < len(data):
        chunk_len = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        if chunk_type == b"IHDR":
            after_ihdr = pos + 8 + chunk_len + 4
            text_data = b"Comment\x00broken"
            corrupt_chunk = struct.pack(">I", len(text_data)) + b"tEXt" + text_data + b"\x00\x00\x00\x00"
            path.write_bytes(data[:after_ihdr] + corrupt_chunk + data[after_ihdr:])
            return
        pos += 8 + chunk_len + 4
    raise AssertionError("IHDR が見つからない")


def test_open_image_with_exif_normal(tmp_path: pathlib.Path) -> None:
    """EXIF情報のない画像を正常に開ける。フォールバックフラグは False。"""
    path = tmp_path / "test.png"
    _make_png(path, size=(80, 60))
    img, used_fallback = imageconverter.open_image_with_exif(path)
    assert img.width == 80
    assert img.height == 60
    assert used_fallback is False


def test_open_image_with_exif_fallback_on_exif_error(tmp_path: pathlib.Path) -> None:
    """exif_transpose が例外を送出した場合、copy() にフォールバックする。"""
    path = tmp_path / "test.png"
    _make_png(path, size=(40, 30))
    with (
        unittest.mock.patch(
            "pytools.imageconverter.PIL.ImageOps.exif_transpose",
            side_effect=Exception("exif error"),
        ),
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        img, used_fallback = imageconverter.open_image_with_exif(path)
    assert img.width == 40
    assert img.height == 30
    # 例外発生時は warnings.warn が呼ばれる
    assert any("exif error" in str(w.message) for w in caught)
    # exif_transpose 例外は読み込みフォールバックとは独立
    assert used_fallback is False


def test_open_image_with_exif_truncated_fallback(tmp_path: pathlib.Path) -> None:
    """tEXt チャンクの CRC 不整合 PNG は寛容モードで読み込まれフラグが立つ。"""
    path = tmp_path / "corrupt.png"
    _make_corrupt_text_png(path)
    # 既定モードでは Pillow が読み込みに失敗することを事前確認する
    PIL.ImageFile.LOAD_TRUNCATED_IMAGES = False
    with pytest.raises(PIL.UnidentifiedImageError), PIL.Image.open(path) as img:
        img.load()
    # open_image_with_exif は内部で寛容モードへフォールバックする
    img, used_fallback = imageconverter.open_image_with_exif(path)
    assert used_fallback is True
    assert img.width == 50
    assert img.height == 50
    # フラグはコンテキスト終了時に元に戻っている
    assert PIL.ImageFile.LOAD_TRUNCATED_IMAGES is False


def test_convert_directory_to_jpeg(tmp_path: pathlib.Path) -> None:
    _make_png(tmp_path / "a.png")
    _make_png(tmp_path / "b.png")
    events = imageconverter.convert_directory(tmp_path, output_type="jpeg", max_width=200, max_height=200)
    assert not events
    assert (tmp_path / "a.jpg").exists()
    assert (tmp_path / "b.jpg").exists()
    assert not (tmp_path / "a.png").exists()


def test_convert_directory_resizes_large_image(tmp_path: pathlib.Path) -> None:
    _make_png(tmp_path / "big.png", size=(4000, 3000))
    imageconverter.convert_directory(tmp_path, output_type="jpeg", max_width=2048, max_height=1536)
    out = tmp_path / "big.jpg"
    assert out.exists()
    with PIL.Image.open(out) as img:
        assert img.width <= 2048
        assert img.height <= 1536


def test_convert_directory_keeps_text(tmp_path: pathlib.Path) -> None:
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")
    _make_png(tmp_path / "a.png")
    imageconverter.convert_directory(tmp_path, output_type="jpeg")
    assert (tmp_path / "readme.txt").exists()
    assert (tmp_path / "a.jpg").exists()


@pytest.mark.parametrize("output_type", ["jpeg", "png", "webp"])
def test_convert_directory_output_types(tmp_path: pathlib.Path, output_type: imageconverter.OutputType) -> None:
    _make_png(tmp_path / "a.png", size=(50, 50))
    imageconverter.convert_directory(tmp_path, output_type=output_type)
    suffix = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[output_type]
    assert (tmp_path / f"a{suffix}").exists()


def test_convert_directory_corrupt_text_png_is_warning(tmp_path: pathlib.Path) -> None:
    """tEXt CRC 不整合の PNG は寛容モードで変換され、警告として戻り値に含まれる。"""
    src = tmp_path / "corrupt.png"
    _make_corrupt_text_png(src)
    events = imageconverter.convert_directory(tmp_path, output_type="jpeg", max_width=2048, max_height=1536)
    out = tmp_path / "corrupt.jpg"
    assert out.exists(), "寛容モードフォールバックで変換は成功する"
    assert not src.exists(), "元 PNG は変換後にリネーム消費される"
    assert len(events) == 1
    path, severity, _msg = events[0]
    assert path == out
    assert severity == "warning"


def test_convert_directory_completely_broken_file_is_error(tmp_path: pathlib.Path) -> None:
    """PNG ヘッダーすら無効なファイルは寛容モードでも読めず、失敗として戻り値に含まれる。"""
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a PNG file at all")
    events = imageconverter.convert_directory(tmp_path, output_type="jpeg", remove_failed=True)
    assert not bad.exists(), "remove_failed=True で削除される"
    assert len(events) == 1
    path, severity, _msg = events[0]
    assert path == bad
    assert severity == "error"


def test_convert_directory_non_image_extension_excluded_from_events(tmp_path: pathlib.Path) -> None:
    """Pillow が画像と見なさない拡張子の失敗は戻り値に含まれない。"""
    bad = tmp_path / "data.bin"
    bad.write_bytes(b"random binary blob")
    events = imageconverter.convert_directory(tmp_path, output_type="jpeg", remove_failed=True)
    # _IGNORE_SUFFIXES に該当しないため処理対象に入るが、Pillow 画像拡張子ではないため event には積まれない
    assert not events
