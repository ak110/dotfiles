"""画像変換。

ディレクトリ配下の画像を指定形式へ一括変換し、最大辺サイズを超える場合は縮小する。
CLI からは `py-imageconverter` コマンドとして、他モジュールからは `convert_directory`
関数として呼び出せる。
"""

import argparse
import logging
import pathlib
import secrets
import typing
import warnings

import natsort
import numpy as np
import PIL.Image
import PIL.ImageOps
import tqdm

logger = logging.getLogger(__name__)

_IGNORE_SUFFIXES = {".db", ".txt", ".htm", ".html", ".pdf"}
_TYPE_SUFFIXES = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}

OutputType = typing.Literal["jpeg", "png", "webp"]


def open_image_with_exif(path: pathlib.Path) -> PIL.Image.Image:
    """EXIF情報に従い回転補正した画像を返す。

    `PIL.ImageOps.exif_transpose`が例外を送出した場合、元画像のコピーに
    フォールバックする。
    """
    img = PIL.Image.open(path)
    try:
        return PIL.ImageOps.exif_transpose(img)
    except Exception as e:
        warnings.warn(f"{type(e).__name__}: {e}", stacklevel=2)
        return img.copy()


def _main() -> None:
    parser = argparse.ArgumentParser(description="画像変換")
    parser.add_argument("--output-type", default="jpeg", choices=("jpeg", "png", "webp"), nargs="?")
    parser.add_argument("--max-width", default=2048, type=int)
    parser.add_argument("--max-height", default=1536, type=int)
    parser.add_argument("--jpeg-quality", default=90, type=int)
    parser.add_argument("--repack-png", action="store_true")
    parser.add_argument("--no-remove-failed", action="store_true")
    parser.add_argument("targets", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    for target_path in args.targets:
        convert_directory(
            target_path,
            output_type=args.output_type,
            max_width=args.max_width,
            max_height=args.max_height,
            jpeg_quality=args.jpeg_quality,
            repack_png=args.repack_png,
            remove_failed=not args.no_remove_failed,
        )


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
) -> None:
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
    """
    suffix = _TYPE_SUFFIXES[output_type]
    paths: list[pathlib.Path] = [p for p in target_path.glob("**/*") if p.is_file() and p.suffix not in _IGNORE_SUFFIXES]
    paths = list(natsort.natsorted(paths))
    pbar = progress if progress is not None else tqdm.tqdm(total=len(paths), ascii=True, ncols=100)
    try:
        for path in paths:
            _convert_one(
                path,
                suffix=suffix,
                output_type=output_type,
                max_width=max_width,
                max_height=max_height,
                jpeg_quality=jpeg_quality,
                repack_png=repack_png,
                remove_failed=remove_failed,
            )
            pbar.update(1)
    finally:
        if progress is None:
            pbar.close()


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
) -> None:
    """1 ファイル分の変換処理。"""
    try:
        # PNG の場合は repack_png を実行
        if repack_png and path.suffix.lower() == ".png":
            temp_png_path = path.parent / f"{secrets.token_urlsafe(8)}.png"
            _repack_png(path, temp_png_path)
            path.unlink()
            temp_png_path.rename(path)
        # 画像を読み込む
        with open_image_with_exif(path) as img:
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
    except Exception as e:
        tqdm.tqdm.write(f"{path}: convert failed ({e})")
        # 失敗したファイルは削除する（バックアップされている前提）
        if remove_failed:
            path.unlink()
        return
    # 元ファイルを削除し、リネームする（バックアップされている前提）
    path.unlink()
    save_path = path.with_suffix(suffix)
    temp_path.rename(save_path)
    tqdm.tqdm.write(str(save_path))


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
