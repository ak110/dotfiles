"""imageconverter モジュールの単体テスト。"""

import pathlib
import unittest.mock
import warnings

import PIL.Image
import pytest

from pytools import imageconverter


def _make_png(path: pathlib.Path, size: tuple[int, int] = (100, 100)) -> None:
    PIL.Image.new("RGB", size, color=(255, 0, 0)).save(path, format="PNG")


def test_open_image_with_exif_normal(tmp_path: pathlib.Path) -> None:
    """EXIF情報のない画像を正常に開ける。"""
    path = tmp_path / "test.png"
    _make_png(path, size=(80, 60))
    img = imageconverter.open_image_with_exif(path)
    assert img.width == 80
    assert img.height == 60


def test_open_image_with_exif_fallback_on_error(tmp_path: pathlib.Path) -> None:
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
        img = imageconverter.open_image_with_exif(path)
    assert img.width == 40
    assert img.height == 30
    # 例外発生時は warnings.warn が呼ばれる
    assert any("exif error" in str(w.message) for w in caught)


def test_convert_directory_to_jpeg(tmp_path: pathlib.Path) -> None:
    _make_png(tmp_path / "a.png")
    _make_png(tmp_path / "b.png")
    imageconverter.convert_directory(tmp_path, output_type="jpeg", max_width=200, max_height=200)
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
