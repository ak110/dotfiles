"""ディレクトリツリーの差分同期 (C# clonedir の Python 移植)。

送り側の内容を受け側へ複製する。既定では更新モード (送り側が新しいものだけコピー)。
`--mirror` で受け側の不要ファイル・ディレクトリも削除する。
"""

import argparse
import fnmatch
import logging
import pathlib
import shutil

logger = logging.getLogger(__name__)


def _main() -> None:
    parser = argparse.ArgumentParser(description="ディレクトリを同期する")
    parser.add_argument("src", type=pathlib.Path, help="送り側ディレクトリ")
    parser.add_argument("dst", type=pathlib.Path, help="受け側ディレクトリ")
    parser.add_argument("--exclude", type=pathlib.Path, help="除外パターンを改行区切りで書いたファイル")
    parser.add_argument("-m", "--mirror", action="store_true", help="受け側の不要ファイルを削除する")
    parser.add_argument("-t", "--top-only", action="store_true", help="最上位ディレクトリのみ処理する")
    parser.add_argument("-d", "--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    excludes = _load_excludes(args.exclude)
    clone(args.src, args.dst, excludes=excludes, mirror=args.mirror, top_only=args.top_only, dry_run=args.dry_run)


def _load_excludes(path: pathlib.Path | None) -> list[str]:
    if path is None:
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_excluded(path: pathlib.Path, excludes: list[str]) -> bool:
    text = path.as_posix()
    return any(fnmatch.fnmatch(text, pattern) or pattern in text for pattern in excludes)


def clone(
    src: pathlib.Path,
    dst: pathlib.Path,
    *,
    excludes: list[str] | None = None,
    mirror: bool = False,
    top_only: bool = False,
    dry_run: bool = False,
) -> None:
    """`src` から `dst` へツリーを同期する。"""
    excludes = excludes or []
    src = src.resolve()
    dst = dst.resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"転送元ディレクトリが存在しません: {src}")
    if not dst.exists():
        logger.info("mkdir: %s", dst)
        if not dry_run:
            dst.mkdir(parents=True)

    # ディレクトリ作成
    if not top_only:
        for sub in src.rglob("*"):
            if not sub.is_dir() or _is_excluded(sub, excludes):
                continue
            rel = sub.relative_to(src)
            target = dst / rel
            if not target.exists():
                logger.info("mkdir: %s", target)
                if not dry_run:
                    target.mkdir(parents=True)

    # ファイルコピー・更新
    iterator = src.iterdir() if top_only else src.rglob("*")
    for sub in iterator:
        if not sub.is_file() or _is_excluded(sub, excludes):
            continue
        rel = sub.relative_to(src)
        target = dst / rel
        if not target.exists():
            logger.info("create: %s", target)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sub, target)
            continue
        src_mtime = sub.stat().st_mtime
        dst_mtime = target.stat().st_mtime
        needs_copy = src_mtime != dst_mtime if mirror else dst_mtime < src_mtime
        if needs_copy:
            logger.info("update: %s", target)
            if not dry_run:
                shutil.copy2(sub, target)

    # 不要ファイル・ディレクトリ削除 (ミラーモード以外でも受け側だけにあるものは除去するのが元実装)
    dst_dirs = [] if top_only else [d for d in dst.rglob("*") if d.is_dir() and not _is_excluded(d, excludes)]
    dst_files = list(dst.iterdir()) if top_only else list(dst.rglob("*"))
    for sub in dst_files:
        if not sub.is_file() or _is_excluded(sub, excludes):
            continue
        rel = sub.relative_to(dst)
        src_equivalent = src / rel
        if not src_equivalent.exists():
            logger.info("delete: %s", sub)
            if not dry_run:
                sub.unlink()
    for sub in sorted(dst_dirs, key=lambda p: -len(p.parts)):
        rel = sub.relative_to(dst)
        src_equivalent = src / rel
        if not src_equivalent.exists():
            logger.info("rmdir: %s", sub)
            if not dry_run:
                shutil.rmtree(sub, ignore_errors=True)


if __name__ == "__main__":
    _main()
