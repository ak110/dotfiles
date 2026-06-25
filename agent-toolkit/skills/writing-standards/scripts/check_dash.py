#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Markdownの地の文・見出し中のダッシュ系禁止文字を検査する独立スクリプト。

writing-standards SKILL.mdの「emダッシュ・horizontal bar・2倍ダッシュは
日本語の地の文・見出しで使わない」規定を機械化する。
検出対象はU+2014（EM DASH）・U+2015（HORIZONTAL BAR）・U+2500の2連続（2倍ダッシュ）。
フェンス付きコードブロック内（バッククォート形式・チルダ形式）およびインラインコード内は除外する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# 抜粋の最大文字数。違反行を見やすく示す切り詰め幅。
_EXCERPT_LIMIT = 80

# ディレクトリ展開時に走査する拡張子。`.md.tmpl`はchezmoi由来の二重拡張子。
_DEFAULT_EXTENSIONS = frozenset({".md", ".md.tmpl"})

# ディレクトリ展開時にスキップするディレクトリ名。VCS管理外・自動生成・依存物を除外する。
# `check_line_width.py`・`check_colloquial.py`の`_EXCLUDED_DIRS`と同一集合。
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

# 検出対象の文字パターン。U+2500は2連続のみを対象とする。
_DASH_PATTERN = re.compile(r"—|―|──")

# 違反種別の表示名。
_KIND_MAP = {
    "—": "em-dash(U+2014)",
    "―": "horizontal-bar(U+2015)",
    "──": "double-dash(U+2500x2)",
}

# フェンス開始の最小バッククォート/チルダ数。
_FENCE_RE = re.compile(r"^( {0,3})(```+|~~~+)")


def main() -> int:
    """ダッシュ系禁止文字の検査エントリポイント。"""
    parser = argparse.ArgumentParser(
        description="Markdownの地の文・見出し中のダッシュ系禁止文字を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のMarkdownファイルまたはディレクトリ（複数指定可）",
    )
    args = parser.parse_args()

    targets = _expand_paths(args.paths)
    all_violations: list[str] = []
    for path in targets:
        all_violations.extend(_check_file(path))

    for line in all_violations:
        print(line, file=sys.stderr)
    return 1 if all_violations else 0


def _expand_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """ファイル/ディレクトリ混在の入力を検査対象ファイルの一覧へ展開する。

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


def _check_file(path: pathlib.Path) -> list[str]:
    """1ファイルを検査して違反行のメッセージ一覧を返す。読み込み失敗時は空リストを返す。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    violations: list[str] = []
    in_fence = False
    fence_marker = ""

    for lineno, raw in enumerate(text.splitlines(), start=1):
        # フェンス開閉判定。バッククォート3個以上またはチルダ3個以上。
        m = _FENCE_RE.match(raw)
        if m:
            marker = m.group(2)
            if not in_fence:
                in_fence = True
                # 先頭文字（`か~か）のみ保持し、閉じ判定に使う。
                fence_marker = marker[0]
            elif marker[0] == fence_marker[0] and len(marker) >= 3:
                in_fence = False
                fence_marker = ""
            continue

        if in_fence:
            continue

        # インラインコードを除去してからダッシュを検索する。
        searchable = _strip_inline_code(raw)
        for match in _DASH_PATTERN.finditer(searchable):
            matched = match.group(0)
            kind = _KIND_MAP[matched]
            # インラインコードは同一文字数の空白で置換済みのため、除去後オフセット＝元行オフセット。
            col = match.start() + 1
            excerpt = raw if len(raw) <= _EXCERPT_LIMIT else raw[:_EXCERPT_LIMIT] + "…"
            violations.append(f'{path}:{lineno}:{col}: {kind} "{excerpt}"')

    return violations


def _strip_inline_code(line: str) -> str:
    """行中のバッククォートで囲まれたインラインコードを空白で置換する。

    マッチしたスパンを同じ長さの空白に置換することで、他の位置の列番号がずれない。
    バッククォートが閉じていない（奇数個で終わる）場合はそのまま返す。
    """
    result = list(line)
    i = 0
    while i < len(line):
        if line[i] == "`":
            # バッククォートの連続長を数える（開きバッククォートの個数）。
            j = i
            while j < len(line) and line[j] == "`":
                j += 1
            tick_len = j - i
            # 同じ長さの閉じバッククォートを探す。
            close_pat = "`" * tick_len
            close_idx = line.find(close_pat, j)
            if close_idx != -1:
                # インラインコードスパン全体を空白に置換する。
                end = close_idx + tick_len
                for k in range(i, end):
                    result[k] = " "
                i = end
            else:
                i = j
        else:
            i += 1
    return "".join(result)


if __name__ == "__main__":
    sys.exit(main())
