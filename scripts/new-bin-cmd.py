#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""bin/ 配下の Linux/Windows ラッパースクリプトペアを生成するジェネレーター。

使い方:
    scripts/new-bin-cmd <name> <command...>

引数:
    name    スクリプト名（bin/<name> および bin/<name>.cmd を生成する）
    command 実行するコマンド（複数トークンはスペースで連結する）

生成物:
    bin/<name>     (bash, UTF-8, LF, 実行権限付き)
    bin/<name>.cmd (CP932, CRLF)

副作用:
    docs/development/development.md のプラットフォーム対応ファイル一覧へエントリをアルファベット順に挿入する。

冪等性:
    既存エントリがあればスキップする。
"""

import argparse
import re
import stat
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_BIN_DIR = _REPO_ROOT / "bin"
_DEVELOPMENT_MD = _REPO_ROOT / "docs" / "development" / "development.md"


def main(argv: list[str] | None = None) -> int:
    """コマンドラインエントリーポイント。"""
    parser = argparse.ArgumentParser(description="bin/ 配下の Linux/Windows ラッパースクリプトペアを生成する。")
    parser.add_argument("name", help="スクリプト名")
    parser.add_argument("command", nargs="+", help="実行するコマンド（複数トークンはスペースで連結）")
    args = parser.parse_args(argv)

    name: str = args.name
    command: str = " ".join(args.command)

    _generate_bash(name, command)
    _generate_cmd(name, command)
    _update_development_md(name)

    return 0


def _generate_bash(name: str, command: str) -> None:
    path = _BIN_DIR / name
    if path.exists():
        print(f"スキップ（既存）: {path.relative_to(_REPO_ROOT)}")
        return
    content = f'#!/bin/bash\n# NOTE: 対応するWindows版 → bin/{name}.cmd\nexec {command} "$@"\n'
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"生成: {path.relative_to(_REPO_ROOT)}")


def _generate_cmd(name: str, command: str) -> None:
    path = _BIN_DIR / f"{name}.cmd"
    if path.exists():
        print(f"スキップ（既存）: {path.relative_to(_REPO_ROOT)}")
        return
    content = f"@echo off\r\nrem NOTE: 対応するLinux版 → bin/{name}\r\n{command} %*\r\n"
    # CP932エンコード + CRLFで直接書き込む（iconv不要）
    path.write_bytes(content.encode("cp932"))
    print(f"生成: {path.relative_to(_REPO_ROOT)}")


def _update_development_md(name: str) -> None:
    """プラットフォーム対応ファイル一覧へエントリをアルファベット順に挿入する。"""
    entry = f"- `bin/{name}` ↔ `bin/{name}.cmd`"
    text = _DEVELOPMENT_MD.read_text(encoding="utf-8")

    if entry in text.splitlines():
        print(f"スキップ（既存）: development.md に bin/{name} は既に存在します")
        return

    # bin/* ペアのエントリ群を探す
    block_re = re.compile(
        r"((?:^- `bin/[^`]+` ↔ `bin/[^`]+\.cmd`\n)+)",
        re.MULTILINE,
    )
    match = block_re.search(text)
    if not match:
        print(f"警告: development.md のプラットフォーム対応ファイル一覧が見つかりません。手動で追加してください: {entry}")
        return

    entries = sorted([e for e in match.group(1).splitlines() if e.strip()] + [entry])
    new_block = "\n".join(entries) + "\n"
    new_text = text[: match.start(1)] + new_block + text[match.end(1) :]
    _DEVELOPMENT_MD.write_text(new_text, encoding="utf-8")
    print(f"更新: docs/development/development.md に bin/{name} を追加")


if __name__ == "__main__":
    sys.exit(main())
