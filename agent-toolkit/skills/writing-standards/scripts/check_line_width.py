#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Markdownの1行表示幅を半角換算で検査する独立スクリプト。

writing-standards SKILL.mdの「1行の表示幅は半角換算で127までを上限」規約を機械化する。
textlintやmarkdownlintには全角2・半角1で判定する既製ルールがないため本スクリプトを設ける。
引数はファイルおよびディレクトリの混在入力を受け付け、ディレクトリは再帰走査する。
フェンス付きコードブロックとMarkdown表（パイプ`|`で始まる行）は検査対象外とする。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import unicodedata

_DEFAULT_WIDTH = 127
# 先頭抜粋の最大半角換算幅。違反行を見やすく示す切り詰め幅。
_EXCERPT_WIDTH = 60

# ディレクトリ展開時に走査する拡張子。Markdown本文と二重拡張子`.md.tmpl`を対象とする。
# `.md.tmpl`はchezmoiテンプレート由来の二重拡張子。`pathlib.Path.suffix`は最後の要素のみを返すため、
# 末尾一致判定で複合拡張子も拾う。`.tmpl`単独はテンプレート構文を含み行幅検査の対象としない。
_DEFAULT_EXTENSIONS = frozenset({".md", ".md.tmpl"})

# ディレクトリ展開時にスキップするディレクトリ名。VCS管理外・自動生成・依存物を除外する。
# `check_dash.py`の`_EXCLUDED_DIRS`と同一集合。
_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "site",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".vscode",
    }
)


def main() -> int:
    """Markdownの1行表示幅を半角換算で検査するエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="Markdownの1行表示幅（半角換算）を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のMarkdownファイルまたはディレクトリ（複数指定可）",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=_DEFAULT_WIDTH,
        help=f"半角換算の上限幅（既定: {_DEFAULT_WIDTH}）",
    )
    args = parser.parse_args()

    targets = _expand_paths(args.paths)
    all_violations: list[str] = []
    for path in targets:
        all_violations.extend(_check_file(path, args.width))

    for line in all_violations:
        print(line, file=sys.stderr)
    return 1 if all_violations else 0


def _expand_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """ファイル/ディレクトリ混在の入力を、検査対象ファイルの一覧へ展開する。

    ディレクトリは再帰的に対象拡張子のファイルを収集する。
    `_EXCLUDED_DIRS`配下は除外する。順序の安定性のため、ディレクトリ展開分はpath順に並べる。
    """
    expanded: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for p in paths:
        if p.is_file():
            _add(expanded, seen, p)
        elif p.is_dir():
            for sub in sorted(p.rglob("*")):
                if not sub.is_file():
                    continue
                # 除外判定は引数ディレクトリ`p`からの相対パス成分のみで行う。
                # 絶対パス全体（`sub.parts`）で判定すると、引数ディレクトリ自身が`site`・`dist`等の
                # 汎用名を含む場合に配下全体が誤って除外される。
                if any(part in _EXCLUDED_DIRS for part in sub.relative_to(p).parts):
                    continue
                name_lower = sub.name.lower()
                if not any(name_lower.endswith(ext) for ext in _DEFAULT_EXTENSIONS):
                    continue
                _add(expanded, seen, sub)
    return expanded


def _add(out: list[pathlib.Path], seen: set[pathlib.Path], path: pathlib.Path) -> None:
    """重複を除き出力リストへ追加する。"""
    resolved = path.resolve()
    if resolved in seen:
        return
    seen.add(resolved)
    out.append(path)


def _check_file(path: pathlib.Path, max_width: int) -> list[str]:
    """1ファイルを検査して違反行のメッセージ一覧を返す。読み込み失敗時は空リストを返す。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    in_fence = False
    fence_marker = ""
    violations: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if _is_fence(raw):
            stripped = raw.lstrip()
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        if _is_table_row(raw):
            continue
        width = _display_width(raw)
        if width > max_width:
            excerpt = _truncate(raw, _EXCERPT_WIDTH)
            violations.append(f"{path}:{lineno} 幅={width} {excerpt}")
    return violations


def _is_fence(line: str) -> bool:
    """フェンス開閉行かどうかを判定する。先頭の連続スペースは無視する。"""
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _is_table_row(line: str) -> bool:
    """Markdown表の行であるか判定する。パイプ`|`で始まる行を表として扱う。"""
    return line.lstrip().startswith("|")


def _display_width(text: str) -> int:
    """半角換算の表示幅を返す。全角を2、半角を1として計算する。"""
    return sum(_char_width(c) for c in text)


def _truncate(text: str, max_width: int) -> str:
    """半角換算幅で先頭から切り詰めた抜粋を返す。"""
    width = 0
    out: list[str] = []
    for c in text:
        w = _char_width(c)
        if width + w > max_width:
            out.append("…")
            break
        out.append(c)
        width += w
    return "".join(out)


def _char_width(c: str) -> int:
    """1文字の半角換算幅を返す。Ambiguous文字は全角端末を想定して2とする。"""
    return 2 if unicodedata.east_asian_width(c) in ("F", "W", "A") else 1


if __name__ == "__main__":
    sys.exit(main())
