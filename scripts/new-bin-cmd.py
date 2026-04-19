#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""bin/ 配下の Linux/Windows ラッパースクリプトペアを生成するジェネレーター。

使い方:
    scripts/new-bin-cmd <name> <command...>

引数:
    name    スクリプト名（executable_<name> および executable_<name>.cmd を生成する）
    command 実行するコマンド（複数トークンはスペースで連結する）

生成物:
    .chezmoi-source/bin/executable_<name>     (bash, UTF-8, LF, 実行権限付き)
    .chezmoi-source/bin/executable_<name>.cmd (CP932, CRLF)

副作用:
    .chezmoi-source/.chezmoiignore の Linux 除外ブロックへ bin/<name>.cmd をアルファベット順に挿入する。
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
_CHEZMOI_SRC = _REPO_ROOT / ".chezmoi-source"
_BIN_DIR = _CHEZMOI_SRC / "bin"
_CHEZMOIIGNORE = _CHEZMOI_SRC / ".chezmoiignore"
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
    _update_chezmoiignore(name)
    _update_development_md(name)

    return 0


def _generate_bash(name: str, command: str) -> None:
    path = _BIN_DIR / f"executable_{name}"
    if path.exists():
        print(f"スキップ（既存）: {path.relative_to(_REPO_ROOT)}")
        return
    content = f'#!/bin/bash\n# NOTE: 対応するWindows版 → bin/executable_{name}.cmd\nexec {command} "$@"\n'
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"生成: {path.relative_to(_REPO_ROOT)}")


def _generate_cmd(name: str, command: str) -> None:
    path = _BIN_DIR / f"executable_{name}.cmd"
    if path.exists():
        print(f"スキップ（既存）: {path.relative_to(_REPO_ROOT)}")
        return
    content = f"@echo off\r\nrem NOTE: 対応するLinux版 → bin/executable_{name}\r\n{command} %*\r\n"
    # CP932エンコード + CRLFで直接書き込む（iconv不要）
    path.write_bytes(content.encode("cp932"))
    print(f"生成: {path.relative_to(_REPO_ROOT)}")


def _update_chezmoiignore(name: str) -> None:
    """Linux 除外ブロックへ bin/<name>.cmd をアルファベット順に挿入する。"""
    entry = f"bin/{name}.cmd"
    text = _CHEZMOIIGNORE.read_text(encoding="utf-8")

    block_re = re.compile(
        r"(# Linux: Windows専用ファイルをスキップ\n"
        r'\{\{ if ne \.chezmoi\.os "windows" \}\}\n)'
        r"(.*?)"
        r"(\{\{ end \}\})",
        re.DOTALL,
    )
    match = block_re.search(text)
    if not match:
        print(f"警告: .chezmoiignore のLinux除外ブロックが見つかりません。手動で追加してください: {entry}")
        return

    entries_str = match.group(2)
    if entry in entries_str.splitlines():
        print(f"スキップ（既存）: .chezmoiignore に {entry} は既に存在します")
        return

    entries = sorted([e for e in entries_str.splitlines() if e.strip()] + [entry])
    new_entries_str = "\n".join(entries) + "\n"
    new_text = text[: match.start(2)] + new_entries_str + text[match.start(3) :]
    _CHEZMOIIGNORE.write_text(new_text, encoding="utf-8")
    print(f"更新: .chezmoi-source/.chezmoiignore に {entry} を追加")


def _update_development_md(name: str) -> None:
    """プラットフォーム対応ファイル一覧へエントリをアルファベット順に挿入する。"""
    entry = f"- `.chezmoi-source/bin/executable_{name}` ↔ `.chezmoi-source/bin/executable_{name}.cmd`"
    text = _DEVELOPMENT_MD.read_text(encoding="utf-8")

    if f"executable_{name}`" in text:
        print(f"スキップ（既存）: development.md に executable_{name} は既に存在します")
        return

    # bin/executable_* ペアのエントリ群を探す
    block_re = re.compile(
        r"((?:^- `\.chezmoi-source/bin/executable_[^`]+` ↔ `\.chezmoi-source/bin/executable_[^`]+`\n)+)",
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
    print(f"更新: docs/development/development.md に executable_{name} を追加")


if __name__ == "__main__":
    sys.exit(main())
