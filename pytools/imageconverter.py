"""画像変換。"""

import argparse
import pathlib
import secrets
import warnings

import natsort
import numpy as np
import PIL.Image
import PIL.ImageOps
import tqdm

_IGNORE_SUFFIXES = {".db", ".txt", ".htm", ".html", ".pdf"}
_TYPE_SUFFIXES = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}


def _main():
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
        _convert(args, target_path)


def _convert(args, target_path):
    suffix = _TYPE_SUFFIXES[args.output_type]
    paths: list[pathlib.Path] = [p for p in target_path.glob("**/*") if p.is_file() and p.suffix not in _IGNORE_SUFFIXES]
    paths = list(natsort.natsorted(paths))
    for path in tqdm.tqdm(paths, ascii=True, ncols=100):
        try:
            # PNGならrepack_png
            if args.repack_png and path.suffix.lower() == ".png":
                temp_png_path = path.parent / f"{secrets.token_urlsafe(8)}.png"
                _repack_png(path, temp_png_path)
                path.unlink()
                temp_png_path.rename(path)
            # 読み込み
            with PIL.Image.open(path) as img_file:
                try:
                    img = PIL.ImageOps.exif_transpose(img_file)
                except Exception as e:
                    warnings.warn(f"{type(e).__name__}: {e}", stacklevel=1)
                    img = img_file.copy()
                # JPEG向け色変換
                if args.output_type == "jpeg":
                    if img.mode == "RGBA":
                        img = img.convert("RGB")
                    elif img.mode == "LA":
                        img = img.convert("L")
                # 縮小
                if img.width >= args.max_width or img.height >= args.max_height:
                    r = min(args.max_width / img.width, args.max_height / img.height)
                    size = int(img.width * r), int(img.height * r)
                    img = img.resize(size, resample=PIL.Image.Resampling.LANCZOS)
                # メタデータを消して保存
                with PIL.Image.fromarray(np.asarray(img)) as img2:
                    temp_path = path.parent / f"{secrets.token_urlsafe(8)}{suffix}"
                    if args.output_type == "jpeg":
                        img2.save(temp_path, format="JPEG", quality=args.jpeg_quality)
                    else:
                        img2.save(temp_path)
        except Exception as e:
            tqdm.tqdm.write(f"{path}: convert failed ({e})")
            # 失敗したものは削除する（バックアップされてる前提）
            if not args.no_remove_failed:
                path.unlink()
            continue
        # 削除＆リネーム（バックアップされてる前提）
        path.unlink()
        save_path = path.with_suffix(suffix)
        temp_path.rename(save_path)
        tqdm.tqdm.write(str(save_path))


def _repack_png(input_path: pathlib.Path, output_path: pathlib.Path) -> None:
    """PNGを再パックして不要なチャンクを削除する。wand (ImageMagick) が必要。"""
    try:
        import wand.color  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
        import wand.image  # pyright: ignore[reportMissingImports]  # ty: ignore[unresolved-import]
    except ImportError:
        raise RuntimeError(
            "--repack-png を使用するには wand パッケージと ImageMagick のインストールが必要です。\n"
            "  pip install wand  # または uv pip install wand\n"
            "  # ImageMagick: https://imagemagick.org/script/download.php"
        ) from None
    with wand.image.Image(filename=str(input_path)) as img:
        img.options["png:exclude-chunk"] = "all"
        img.strip()
        with wand.color.Color("transparent"):
            img.save(filename=str(output_path))


if __name__ == "__main__":
    _main()
