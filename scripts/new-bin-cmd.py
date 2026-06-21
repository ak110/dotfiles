#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""bin/配下のLinux/Windowsラッパースクリプトペアを生成するジェネレーター。

`bin/<name>`（bash、UTF-8、LF、実行権限付き）と`bin/<name>.cmd`（CP932、CRLF）のペアを生成する。
既存ファイルがあればスキップする。

ハイフン始まりのトークンを含むコマンドを渡す場合は`--`セパレータで位置引数領域を明示する。
例: `new-bin-cmd.py opus -- claude --model=opus --permission-mode=auto`
"""

import argparse
import stat
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_BIN_DIR = _REPO_ROOT / "bin"


def main(argv: list[str] | None = None) -> int:
    """bin/配下のLinux/Windowsラッパースクリプトペアを生成する。"""
    parser = argparse.ArgumentParser(description="bin/ 配下の Linux/Windows ラッパースクリプトペアを生成する。")
    parser.add_argument("name", help="スクリプト名")
    parser.add_argument(
        "command",
        nargs="+",
        help=(
            "実行するコマンド（複数トークンはスペースで連結）。"
            'ハイフン始まりのトークンを含む場合は"--"セパレータ後に置く（例: opus -- claude --model=opus）'
        ),
    )
    args = parser.parse_args(argv)

    name: str = args.name
    command: str = " ".join(args.command)

    _generate_bash(name, command)
    _generate_cmd(name, command)

    return 0


def _generate_bash(name: str, command: str) -> None:
    path = _BIN_DIR / name
    if path.exists():
        print(f"スキップ（既存）: {path.relative_to(_REPO_ROOT)}")
        return
    content = f'#!/usr/bin/env bash\n# NOTE: 対応する Windows 版 → bin/{name}.cmd\nexec {command} "$@"\n'
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"生成: {path.relative_to(_REPO_ROOT)}")


def _generate_cmd(name: str, command: str) -> None:
    path = _BIN_DIR / f"{name}.cmd"
    if path.exists():
        print(f"スキップ（既存）: {path.relative_to(_REPO_ROOT)}")
        return
    content = f"@echo off\r\nrem NOTE: 対応する Linux 版 → bin/{name}\r\n{command} %*\r\n"
    # CP932 エンコード + CRLF で直接書き込む（iconv 不要）
    path.write_bytes(content.encode("cp932"))
    print(f"生成: {path.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
