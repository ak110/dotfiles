"""全追跡ファイルのLF数を検査し、上限超過時だけ整理を求める。

1ファイル約1万トークンの予算を、Markdownは約40〜55、コードは約11トークン/行で
概算した上限を使う（トークン数の保証ではない）。整理後は上限の60%とし、追記の余裕を戻す。
上限超過は導入方針に反するためエラーとし、上限以下には警告を設けない。
"""

import argparse
import io
import os
import pathlib
import subprocess
import sys

from agent_toolkit._git import command as git_command

from pytools._internal.common import format_cli_error

MARKDOWN_LIMIT = 250
MARKDOWN_AFTER_CLEANUP = 150
OTHER_LIMIT = 1000
OTHER_AFTER_CLEANUP = 600
# chezmoiのテンプレート（`*.md.tmpl`）は人が保守するMarkdownの生成元であり、
# pyproject.tomlのMarkdown検査と同じくMarkdownの上限を適用する。
MARKDOWN_SUFFIXES = (".md", ".md.tmpl")


def _git(root: pathlib.Path, *arguments: str, data: bytes | None = None) -> bytes:
    result = git_command.run(["-C", str(root), *arguments], input=data, capture_output=True, check=False, timeout=60)
    if result.returncode:
        decoded = subprocess.CompletedProcess(
            result.args, result.returncode, result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace")
        )
        raise ValueError(f"Gitの照会に失敗: {format_cli_error(decoded)}")
    return result.stdout


def check_repository(directory: pathlib.Path) -> int:
    """指定ディレクトリが属する作業ツリー全体を検査する。"""
    try:
        root = pathlib.Path(os.fsdecode(_git(directory, "rev-parse", "--show-toplevel").rstrip(b"\n")))
        tracked = _git(root, "ls-files", "-z")
        attributes = _git(root, "check-attr", "-z", "--stdin", "linguist-generated", "linguist-vendored", data=tracked)
        fields = attributes.split(b"\0")
        excluded = {
            fields[index]
            for index in range(0, len(fields) - 1, 3)
            if fields[index + 2] not in (b"unspecified", b"unset", b"false")
        }
        diagnostics = []
        for name in tracked.split(b"\0"):
            if not name or name in excluded:
                continue
            relative = os.fsdecode(name)
            path = root / relative
            if path.is_symlink() or not path.is_file():
                continue
            content = path.read_bytes()
            if b"\0" in content:
                continue
            lines = content.count(b"\n")
            limit, after = (
                (MARKDOWN_LIMIT, MARKDOWN_AFTER_CLEANUP)
                if path.name.endswith(MARKDOWN_SUFFIXES)
                else (OTHER_LIMIT, OTHER_AFTER_CLEANUP)
            )
            if lines > limit:
                diagnostics.append(
                    f"{relative}:1: {lines}行が上限{limit}行を超えている。次の操作: "
                    f"分割・統合・削除で整理後の行数{after}行以下にする。数行の削除で上限以下へ戻さない。"
                )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(
            f"行数の検査に失敗: {error}。次の操作: Gitの実行環境と作業ツリーの読取権限を確認して再実行する。",
            file=sys.stderr,
        )
        return 2
    for diagnostic in diagnostics:
        print(diagnostic)
    return 1 if diagnostics else 0


def main(argv: list[str] | None = None) -> int:
    """引数なしで現在の作業ツリー全体を検査する。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    return check_repository(pathlib.Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main())
