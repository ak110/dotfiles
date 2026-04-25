# PYTHON_ARGCOMPLETE_OK
"""PDFを画像に変換する。システムにPoppler (pdftoppm) のインストールが必要。"""

import argparse
import pathlib

import tqdm.contrib
from pdf2image import convert_from_path

from pytools._internal.cli import enable_completion


def _main():
    parser = argparse.ArgumentParser(description="PDFを画像に変換する")
    parser.add_argument("pdf_files", nargs="+", type=pathlib.Path, help="変換するPDFファイル")
    parser.add_argument("--format", default="png", choices=("png", "jpeg"), help="出力フォーマット")
    enable_completion(parser)
    args = parser.parse_args()
    for pdf_path in args.pdf_files:
        _convert(pdf_path, args.format)


def _convert(pdf_path: pathlib.Path, fmt: str) -> None:
    """1つのPDFを画像に変換してページごとに保存する。"""
    images = convert_from_path(pdf_path)
    save_dir = pdf_path.parent / pdf_path.stem
    save_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".png" if fmt == "png" else ".jpg"
    for i, image in tqdm.contrib.tenumerate(images, desc=pdf_path.name):
        image.save(save_dir / f"{i + 1:04d}{suffix}", fmt.upper())
    print(f"Saved {len(images)} pages to {save_dir}")


if __name__ == "__main__":
    _main()
