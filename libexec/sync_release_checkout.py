"""公開後の develop checkout を共有依存環境の準備後に進める。pyfltr: allow-comment"""

from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

_PLUGIN = "agent-toolkit"


def sync(checkout: Path, release: str, scratch: Path) -> None:
    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(checkout), *args])

    target = git("rev-parse", "--verify", f"{release}^{{commit}}").strip().decode("ascii")
    initial = git("rev-parse", "HEAD").strip()
    git("merge-base", "--is-ancestor", initial.decode("ascii"), target)

    def check_checkout() -> None:
        if (
            git("symbolic-ref", "HEAD").strip() != b"refs/heads/develop"
            or git("rev-parse", "HEAD").strip() != initial
            or git("status", "--porcelain")
        ):
            raise ValueError("developの作業ツリーが変更されたため、同期しません。")

    check_checkout()
    if initial == target.encode("ascii"):
        return

    archive = git("archive", "--format=tar", target, _PLUGIN)
    with (
        tempfile.TemporaryDirectory(prefix="release-prepare-", dir=scratch) as prepared,
        tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as contents,
    ):
        contents.extractall(prepared, filter="data")
        launcher = Path(prepared) / _PLUGIN / "scripts" / "launch.py"
        subprocess.run(
            [
                "uv",
                "run",
                "--no-cache",
                "--no-config",
                "--no-project",
                "--python",
                "3.15",
                "python",
                str(launcher),
                "--prepare",
                "agent_toolkit/atk.py",
            ],
            check=True,
        )
        check_checkout()
        git("merge", "--ff-only", target)
        if git("rev-parse", "HEAD").strip() != target.encode("ascii"):
            raise ValueError("同期後のHEADが準備した公開コミットと一致しません。")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("release")
    parser.add_argument("scratch", type=Path)
    args = parser.parse_args()
    try:
        sync(args.checkout, args.release, args.scratch)
    except (OSError, subprocess.CalledProcessError, tarfile.TarError, ValueError) as error:
        print(f"ローカル同期に失敗しました: {error}", file=sys.stderr)
        print("診断を記録し、通常のgit mergeで代替せず、公開先のCIを確認してください。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
