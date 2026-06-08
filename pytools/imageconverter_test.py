"""imageconverter モジュールの単体テスト。"""

import io
import pathlib
import struct
import sys
import typing
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


def _argv(*targets: str) -> list[str]:
    """`main()` テスト用の `sys.argv` を組み立てる。"""
    return ["py-imageconverter", *targets]


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


def test_open_image_with_exif_unidentified_propagates(tmp_path: pathlib.Path) -> None:
    """シグネチャ判定で寛容モードでも回復しない非画像は UnidentifiedImageError を伝播する。"""
    path = tmp_path / "not_image.bin"
    path.write_bytes(b"random binary blob")
    with pytest.raises(PIL.UnidentifiedImageError):
        imageconverter.open_image_with_exif(path)


def test_convert_directory_to_jpeg(tmp_path: pathlib.Path) -> None:
    _make_png(tmp_path / "a.png")
    _make_png(tmp_path / "b.png")
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg", max_width=200, max_height=200)
    assert not result.events
    assert result.success_count == 2
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


def test_convert_directory_keeps_bat(tmp_path: pathlib.Path) -> None:
    """`.bat` は `_IGNORE_SUFFIXES` で除外され、変換対象に含まれない。"""
    (tmp_path / "run.bat").write_text("echo hello", encoding="utf-8")
    _make_png(tmp_path / "a.png")
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg")
    assert (tmp_path / "run.bat").exists()
    assert (tmp_path / "a.jpg").exists()
    assert result.success_count == 1


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
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg", max_width=2048, max_height=1536)
    out = tmp_path / "corrupt.jpg"
    assert out.exists(), "寛容モードフォールバックで変換は成功する"
    assert not src.exists(), "元 PNG は変換後にリネーム消費される"
    assert result.success_count == 1
    assert len(result.events) == 1
    path, severity, _msg = result.events[0]
    assert path == out
    assert severity == "warning"


def test_convert_directory_non_image_skipped_without_delete(tmp_path: pathlib.Path) -> None:
    """シグネチャ判定で非画像と判定されたファイルはイベント非記録かつ削除されない。

    中身が `b"not a PNG file at all"` のファイルは `PIL.Image.open()` の
    シグネチャ判定で `UnidentifiedImageError` となり「非画像」扱いになる。
    `remove_failed=True` でも保持される。
    """
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not a PNG file at all")
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg", remove_failed=True)
    assert bad.exists(), "非画像と判定されたファイルは削除されない"
    assert not result.events
    assert result.success_count == 0


def test_convert_directory_random_bin_signature_skipped(tmp_path: pathlib.Path) -> None:
    """`.bin` の非画像バイト列も `PIL.Image.open()` がシグネチャ判定で失敗するためスキップされる。"""
    bad = tmp_path / "data.bin"
    bad.write_bytes(b"random binary blob")
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg", remove_failed=True)
    assert bad.exists(), "非画像と判定されたファイルは削除されない"
    assert not result.events
    assert result.success_count == 0


def test_convert_directory_save_failure_is_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pillow が画像認識後に変換で失敗するケースは error イベントとして記録され、削除される。"""
    _make_png(tmp_path / "a.png", size=(50, 50))

    def fake_save(self: PIL.Image.Image, *args: typing.Any, **kwargs: typing.Any) -> None:
        del self, args, kwargs  # noqa
        raise OSError("forced save failure")

    monkeypatch.setattr(PIL.Image.Image, "save", fake_save)
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg", remove_failed=True)
    assert not (tmp_path / "a.png").exists(), "remove_failed=True で元ファイルは削除される"
    assert not (tmp_path / "a.jpg").exists()
    assert result.success_count == 0
    assert len(result.events) == 1
    path, severity, _msg = result.events[0]
    assert path == tmp_path / "a.png"
    assert severity == "error"


def test_convert_directory_single_file_path(tmp_path: pathlib.Path) -> None:
    """ファイル個別パスを `convert_directory` へ渡しても変換できる。"""
    src = tmp_path / "single.png"
    _make_png(src, size=(60, 40))
    result = imageconverter.convert_directory(src, output_type="jpeg")
    assert (tmp_path / "single.jpg").exists()
    assert not src.exists()
    assert result.success_count == 1
    assert not result.events


def test_convert_directory_extensionless_png(tmp_path: pathlib.Path) -> None:
    """拡張子無しでも中身が PNG ならシグネチャ判定で画像と認識され変換される。"""
    src = tmp_path / "noext"
    buf = io.BytesIO()
    PIL.Image.new("RGB", (40, 30), color=(0, 0, 255)).save(buf, format="PNG")
    src.write_bytes(buf.getvalue())
    result = imageconverter.convert_directory(tmp_path, output_type="jpeg")
    out = tmp_path / "noext.jpg"
    assert out.exists()
    assert not src.exists()
    assert result.success_count == 1


def test_main_recursive_glob_no_duplicate_processing(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`**/*` 再帰展開でディレクトリと配下ファイルが同時に得られても、ファイルは 1 度のみ処理される。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    _make_png(sub / "a.png")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", _argv("**/*"))
    with pytest.raises(SystemExit) as exc:
        imageconverter.main()
    assert exc.value.code == 0
    assert (sub / "a.jpg").exists()
    assert not (sub / "a.png").exists()


@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("zero", 1),
        ("one", 0),
        ("multiple", 0),
        ("success_and_error", 1),
    ],
)
def test_main_exit_code(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_code: int,
) -> None:
    """変換成功 0 件は exit(1)、混在 error は exit(1)、それ以外の成功は exit(0)。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", _argv("*"))

    if scenario in ("one", "multiple", "success_and_error"):
        _make_png(tmp_path / "a.png", size=(40, 30))
    if scenario in ("multiple", "success_and_error"):
        _make_png(tmp_path / "b.png", size=(40, 30))

    if scenario == "success_and_error":
        original_save = PIL.Image.Image.save
        call_count = [0]

        def selective_save(self: PIL.Image.Image, *args: typing.Any, **kwargs: typing.Any) -> None:
            call_count[0] += 1
            if call_count[0] >= 2:
                raise OSError("forced save failure")
            original_save(self, *args, **kwargs)

        monkeypatch.setattr(PIL.Image.Image, "save", selective_save)

    with pytest.raises(SystemExit) as exc:
        imageconverter.main()
    assert exc.value.code == expected_code
