"""指定フォルダ内の画像ファイルのサイズの分布を調べるスクリプト。"""

import argparse
import logging
import pathlib

import numpy as np
import PIL.Image
import PIL.ImageOps
import tqdm
from bashplotlib.histogram import plot_hist

from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target_dir", type=pathlib.Path)
    args = parser.parse_args()
    setup_logging(verbose=True)
    _do_dir(args.target_dir)


def _do_dir(target_dir: pathlib.Path) -> None:
    width_list = []
    height_list = []
    files = [p for p in target_dir.glob("**/*") if p.is_file()]
    for file in tqdm.tqdm(files, ascii=True, ncols=100):
        try:
            with PIL.Image.open(file) as img_file:
                try:
                    img = PIL.ImageOps.exif_transpose(img_file)
                except Exception:
                    img = img_file.copy()
                width_list.append(img.width)
                height_list.append(img.height)
                with tqdm.tqdm.external_write_mode():
                    logger.info(f"{file.relative_to(target_dir)}: width={img.width} height={img.height}")
        except Exception:
            logger.info(f"{file.relative_to(target_dir)}: skip")

    width_list = np.array(width_list)
    height_list = np.array(height_list)
    plot_hist(width_list, title="width", bincount=20, showSummary=True)
    plot_hist(height_list, title="height", bincount=20, showSummary=True)

    sizes = np.sqrt(width_list * height_list)
    plot_hist(sizes, title="sqrt(width * height)", bincount=20, showSummary=True)


if __name__ == "__main__":
    _main()
