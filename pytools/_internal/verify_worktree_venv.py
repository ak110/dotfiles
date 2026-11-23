import argparse
import io
import os
import subprocess
import sys
from pathlib import Path


def _registered_worktrees(worktree: Path) -> list[Path]:
    if not any((directory / ".git").exists() for directory in (worktree, *worktree.parents)):
        return []
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"],
        cwd=worktree,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        diagnostic = os.fsdecode(result.stderr or result.stdout).strip()
        raise ValueError(f"Gitの作業ツリー一覧を確認できない（終了コード {result.returncode}）: {diagnostic}")
    return [
        Path(os.fsdecode(field.removeprefix(b"worktree "))).resolve()
        for field in result.stdout.split(b"\0")
        if field.startswith(b"worktree ")
    ]


def _owner(path: Path, roots: list[Path]) -> Path | None:
    return max((root for root in roots if path.is_relative_to(root)), key=lambda root: len(root.parts), default=None)


def verify(worktree: Path, environment: Path) -> None:
    root = worktree.resolve(strict=True)
    requested = root / environment
    effective = requested.resolve()
    default = root / ".venv"
    if Path(os.path.abspath(requested)) == default and effective != default:
        raise ValueError(f"既定の仮想環境 {default} は別の場所を指す: {effective}")

    roots = _registered_worktrees(root)
    environment_owner = _owner(effective, roots)
    if environment_owner is not None and environment_owner != _owner(root, roots):
        raise ValueError(f"仮想環境 {effective} は別の作業ツリー {environment_owner} に属する")


def main() -> int:
    parser = argparse.ArgumentParser(description="同期先が別の作業ツリーの仮想環境でないことを確認する")
    parser.add_argument("worktree", type=Path, metavar="WORKTREE")
    parser.add_argument("environment", metavar="ENVIRONMENT")
    args = parser.parse_args()
    try:
        verify(args.worktree, Path(args.environment or ".venv"))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        print(f"仮想環境を同期しない: {error}", file=sys.stderr)
        print("次の操作: この作業ツリー専用の仮想環境を指定して再実行する。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
