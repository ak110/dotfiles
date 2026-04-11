"""ランダムバイト列のファイルを生成する (C# randfile の Python 移植)。

セキュリティ用途ではないが、`secrets.token_bytes` を使うことで低エントロピーな
シードに依存しない決定論的でない結果を得る。サイズが大きい場合は 1 MiB ずつ
ストリーミング書き込みする。
"""

import argparse
import logging
import pathlib
import secrets

import tqdm

logger = logging.getLogger(__name__)

_CHUNK = 1 << 20  # 1 MiB


def _main() -> None:
    parser = argparse.ArgumentParser(description="ランダムバイトのファイルを生成する")
    parser.add_argument("filename", nargs="?", type=pathlib.Path, default=pathlib.Path("randfile.dat"))
    parser.add_argument("size", nargs="?", type=int, default=1024, help="生成サイズ (バイト)")
    parser.add_argument("--force", action="store_true", help="既存ファイルを上書きする")
    args = parser.parse_args()
    create(args.filename, args.size, force=args.force)


def create(path: pathlib.Path, size: int, *, force: bool = False) -> None:
    """指定サイズのランダムファイルを生成する。"""
    if path.exists() and not force:
        raise FileExistsError(f"出力先ファイルが既に存在します: {path}")
    remaining = size
    with path.open("wb") as fp, tqdm.tqdm(total=size, unit="B", unit_scale=True, ascii=True, ncols=80) as pbar:
        while remaining > 0:
            n = min(_CHUNK, remaining)
            fp.write(secrets.token_bytes(n))
            remaining -= n
            pbar.update(n)


if __name__ == "__main__":
    _main()
