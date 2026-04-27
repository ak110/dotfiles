# PYTHON_ARGCOMPLETE_OK
"""画像変換。

ディレクトリ配下の画像を指定形式へ一括変換し、最大辺サイズを超える場合は縮小する。
CLI からは `py-imageconverter` コマンドとして、他モジュールからは `convert_directory`
関数として呼び出せる。

画像読み込みは 2 段構えで行う。Pillow 12.x は PNG 補助チャンク（tEXt 等）の CRC が
不整合だと既定で `UnidentifiedImageError` を送出して `Image.open` が失敗するが、
ImageMagick 等の他ツールでは警告レベルで読めるケースがある。そこでまず既定モード
（`PIL.ImageFile.LOAD_TRUNCATED_IMAGES = False`）で開き、読み込み例外が発生した
場合のみ `LOAD_TRUNCATED_IMAGES = True` の状態で再試行する。再試行で読み込めた
場合は変換自体は実施するが警告イベントとして集約に積み、呼び元（repack-archive 等）
がバックアップ保持の判断材料に使えるようにしている。再試行でも読めない場合は
通常の変換失敗として集約に積む。
"""

import argparse
import contextlib
import functools
import logging
import pathlib
import secrets
import sys
import typing
import warnings

import natsort
import numpy as np
import PIL.Image
import PIL.ImageFile
import PIL.ImageOps
import tqdm

from pytools._internal.cli import enable_completion

logger = logging.getLogger(__name__)

_IGNORE_SUFFIXES = {".db", ".txt", ".htm", ".html", ".pdf"}
_TYPE_SUFFIXES = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}

OutputType = typing.Literal["jpeg", "png", "webp"]
EventSeverity = typing.Literal["warning", "error"]
ConvertEvent = tuple[pathlib.Path, EventSeverity, str]


def open_image_with_exif(path: pathlib.Path) -> tuple[PIL.Image.Image, bool]:
    """EXIF情報に従い回転補正した画像と寛容モードフラグを返す。

    まず既定モードで `PIL.Image.open` を試み、`UnidentifiedImageError` 等の
    読み込み例外が発生した場合のみ `PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True`
    に切り替えて再試行する。再試行で成功した場合は第 2 戻り値を `True` として返し、
    呼び元が警告として集約できるようにする。再試行でも失敗した場合は例外を再送出する。

    `PIL.ImageOps.exif_transpose` が例外を送出した場合は元画像のコピーへフォールバックする。

    Returns:
        `(image, used_truncated_fallback)` のタプル。第 2 要素が `True` のとき、
        Pillow が既定モードで読み込みに失敗し寛容モードへフォールバックしたことを示す。
    """
    try:
        img = PIL.Image.open(path)
        img.load()
        used_fallback = False
    except (PIL.UnidentifiedImageError, OSError, SyntaxError):
        with _truncated_images_enabled():
            img = PIL.Image.open(path)
            img.load()
        used_fallback = True

    try:
        result = PIL.ImageOps.exif_transpose(img)
    except Exception as e:  # noqa: BLE001
        warnings.warn(f"{type(e).__name__}: {e}", stacklevel=2)
        result = img.copy()
    return result, used_fallback


@contextlib.contextmanager
def _truncated_images_enabled() -> typing.Iterator[None]:
    """`PIL.ImageFile.LOAD_TRUNCATED_IMAGES` を True にして実行するコンテキスト。"""
    prev = PIL.ImageFile.LOAD_TRUNCATED_IMAGES
    # ty は初期値 ``False`` をリテラル型として推論し、True 代入を弾くが、
    # Pillow 側はこのフラグを書き換え可能なグローバル変数として公開している。
    PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        PIL.ImageFile.LOAD_TRUNCATED_IMAGES = prev


@functools.cache
def _pillow_image_extensions() -> frozenset[str]:
    """Pillow が画像とみなす拡張子集合を返す（小文字・先頭ドット付き）。

    `PIL.Image.registered_extensions()` の戻り値（拡張子→フォーマット名の dict）を
    元に構築する。新形式（heic/avif など）が Pillow に追加されても自動的に追従する。
    """
    return frozenset(PIL.Image.registered_extensions())


def _main() -> None:
    parser = argparse.ArgumentParser(description="画像変換")
    parser.add_argument("--output-type", default="jpeg", choices=("jpeg", "png", "webp"), nargs="?")
    parser.add_argument("--max-width", default=2048, type=int)
    parser.add_argument("--max-height", default=1536, type=int)
    parser.add_argument("--jpeg-quality", default=90, type=int)
    parser.add_argument("--repack-png", action="store_true")
    parser.add_argument("--no-remove-failed", action="store_true")
    parser.add_argument("targets", nargs="+", type=pathlib.Path)
    enable_completion(parser)
    args = parser.parse_args()
    has_error = False
    for target_path in args.targets:
        events = convert_directory(
            target_path,
            output_type=args.output_type,
            max_width=args.max_width,
            max_height=args.max_height,
            jpeg_quality=args.jpeg_quality,
            repack_png=args.repack_png,
            remove_failed=not args.no_remove_failed,
        )
        if any(severity == "error" for _, severity, _ in events):
            has_error = True
    if has_error:
        sys.exit(1)


def convert_directory(
    target_path: pathlib.Path,
    *,
    output_type: OutputType = "jpeg",
    max_width: int = 2048,
    max_height: int = 1536,
    jpeg_quality: int = 90,
    repack_png: bool = False,
    remove_failed: bool = True,
    progress: tqdm.tqdm | None = None,
    log_root: pathlib.Path | None = None,
) -> list[ConvertEvent]:
    """ディレクトリ配下の画像を変換する。

    Args:
        target_path: 処理対象のディレクトリ。
        output_type: 出力形式。
        max_width: 出力最大幅。これを超える場合は縮小する。
        max_height: 出力最大高さ。これを超える場合は縮小する。
        jpeg_quality: JPEG 出力時の品質。
        repack_png: PNG 入力時に wand で再パックしてメタデータを除去する。
        remove_failed: 変換に失敗したファイルを削除する。
        progress: 呼び元で作成済みの tqdm。未指定なら内部で作成する。
        log_root: 進捗ログのパス表示で起点にするディレクトリ。指定時は POSIX 区切りの
            相対パスで表示する。未指定時は絶対パスのまま表示する。

    Returns:
        画像変換の警告・失敗イベント一覧（成功のみのファイルは含まれない）。
        各要素は `(対象ファイルのパス, severity, メッセージ)` の 3 要素タプル。
        `severity` は寛容モードへフォールバックして変換に成功した場合 `"warning"`、
        変換自体が失敗した場合 `"error"`。Pillow が画像とみなす拡張子のファイル
        （`PIL.Image.registered_extensions()` 由来）のみ集約対象に含める。
    """
    suffix = _TYPE_SUFFIXES[output_type]
    paths: list[pathlib.Path] = [p for p in target_path.glob("**/*") if p.is_file() and p.suffix not in _IGNORE_SUFFIXES]
    paths = list(natsort.natsorted(paths))
    pbar = progress if progress is not None else tqdm.tqdm(total=len(paths), desc="convert", unit="file", ascii=True, ncols=100)
    events: list[ConvertEvent] = []
    try:
        for path in paths:
            event = _convert_one(
                path,
                suffix=suffix,
                output_type=output_type,
                max_width=max_width,
                max_height=max_height,
                jpeg_quality=jpeg_quality,
                repack_png=repack_png,
                remove_failed=remove_failed,
                log_root=log_root,
            )
            if event is not None:
                events.append(event)
            pbar.update(1)
    finally:
        if progress is None:
            pbar.close()
    return events


def _convert_one(
    path: pathlib.Path,
    *,
    suffix: str,
    output_type: OutputType,
    max_width: int,
    max_height: int,
    jpeg_quality: int,
    repack_png: bool,
    remove_failed: bool,
    log_root: pathlib.Path | None = None,
) -> ConvertEvent | None:
    """1 ファイル分の変換処理。

    成功時は None。寛容モードで読み込みに成功した場合は `("warning", ...)`、
    変換失敗の場合は `("error", ...)` を返す。Pillow が画像とみなさない拡張子の
    ファイルで失敗した場合はログのみ出力し戻り値には含めない。
    """
    is_pillow_image = path.suffix.lower() in _pillow_image_extensions()
    try:
        # PNG の場合は repack_png を実行
        if repack_png and path.suffix.lower() == ".png":
            temp_png_path = path.parent / f"{secrets.token_urlsafe(8)}.png"
            _repack_png(path, temp_png_path)
            path.unlink()
            temp_png_path.rename(path)
        # 画像を読み込む（補助チャンクの CRC 不整合等は寛容モードで再試行）
        img, used_fallback = open_image_with_exif(path)
        with img:
            # JPEG 出力時の色空間変換
            if output_type == "jpeg":
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                elif img.mode == "LA":
                    img = img.convert("L")
            # 指定サイズを超える場合は縮小する
            if img.width >= max_width or img.height >= max_height:
                r = min(max_width / img.width, max_height / img.height)
                size = int(img.width * r), int(img.height * r)
                img = img.resize(size, resample=PIL.Image.Resampling.LANCZOS)
            # メタデータを削除して保存する
            with PIL.Image.fromarray(np.asarray(img)) as img2:
                temp_path = path.parent / f"{secrets.token_urlsafe(8)}{suffix}"
                if output_type == "jpeg":
                    img2.save(temp_path, format="JPEG", quality=jpeg_quality)
                else:
                    img2.save(temp_path)
    except Exception as e:  # noqa: BLE001
        message = f"convert failed ({e})"
        tqdm.tqdm.write(f"{_format_log_path(path, log_root)}: {message}")
        # 失敗したファイルは削除する（バックアップされている前提）
        if remove_failed:
            path.unlink()
        if is_pillow_image:
            return (path, "error", message)
        return None
    # 元ファイルを削除し、リネームする（バックアップされている前提）
    path.unlink()
    save_path = path.with_suffix(suffix)
    temp_path.rename(save_path)
    tqdm.tqdm.write(_format_log_path(save_path, log_root))
    if used_fallback:
        message = "LOAD_TRUNCATED_IMAGES フォールバックで読み込んだ"
        tqdm.tqdm.write(f"{_format_log_path(save_path, log_root)}: warning ({message})")
        return (save_path, "warning", message)
    return None


def _format_log_path(path: pathlib.Path, log_root: pathlib.Path | None) -> str:
    """進捗ログのパス表示を整形する。

    ``log_root`` が指定され、かつ ``path`` がその配下にある場合のみ POSIX 区切りの
    相対パスへ変換する。それ以外は絶対パス文字列をそのまま返す。
    """
    if log_root is None:
        return str(path)
    try:
        return path.relative_to(log_root).as_posix()
    except ValueError:
        return str(path)


def _repack_png(input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """PNG を再パックして不要なチャンクを削除する。wand (ImageMagick) が必要。"""
    try:
        import wand.color
        import wand.image
    except ImportError:
        raise RuntimeError(
            "--repack-png を使用するには wand パッケージと ImageMagick のインストールが必要です。\n"
            "  pip install wand  # または uv pip install wand\n"
            "  # ImageMagick: https://imagemagick.org/script/download.php"
        ) from None
    with wand.image.Image(filename=str(input_path)) as img:
        assert img.options is not None
        img.options["png:exclude-chunk"] = "all"
        img.strip()
        with wand.color.Color("transparent"):
            img.save(filename=str(output_path))


if __name__ == "__main__":
    _main()
