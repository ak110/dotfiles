import argparse
import io
import os
import sys
from pathlib import Path


def _worktree_root(path: Path) -> Path | None:
    return next((directory for directory in (path, *path.parents) if (directory / ".git").exists()), None)


def verify(worktree: Path, environment: Path) -> None:
    root = worktree.resolve(strict=True)
    requested = root / environment
    effective = requested.resolve()
    default = root / ".venv"
    if Path(os.path.abspath(requested)) == default and effective != default:
        raise ValueError(f"既定の仮想環境 {default} は別の場所を指す: {effective}")

    environment_owner = _worktree_root(effective)
    if environment_owner is not None and environment_owner != _worktree_root(root):
        raise ValueError(f"仮想環境 {effective} は別の作業ツリー {environment_owner} に属する")


def main() -> int:
    parser = argparse.ArgumentParser(description="同期先が別の作業ツリーの仮想環境でないことを確認する")
    parser.add_argument("worktree", type=Path, metavar="WORKTREE")
    parser.add_argument("environment", metavar="ENVIRONMENT")
    args = parser.parse_args()
    try:
        verify(args.worktree, Path(args.environment or ".venv"))
    except (OSError, RuntimeError, ValueError) as error:
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        print(f"仮想環境を同期しない: {error}", file=sys.stderr)
        print("次の操作: この作業ツリー専用の仮想環境を指定して再実行する。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
