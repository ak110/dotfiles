#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown-it-py[linkify]>=4.0.0"]
# ///
"""Markdownの地の文・見出し中のダッシュ系禁止文字を検査する独立スクリプト。

writing-standards SKILL.mdの「emダッシュ・horizontal bar・2倍ダッシュは
日本語の地の文・見出しで使わない」規定を機械化する。
検出対象はU+2014（EM DASH）・U+2015（HORIZONTAL BAR）・U+2500の2連続（2倍ダッシュ）。
コードブロック内（フェンス形式・インデント形式）・インラインコード内・URL内は除外する。
"""

# pylint: disable=duplicate-code  # 各スキルの単独実行性を保ち、同一のCommonMark解析を同期する。

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import markdown_it

# 抜粋の最大文字数。違反行を見やすく示す切り詰め幅。
_EXCERPT_LIMIT = 80

# ディレクトリ展開時に走査する拡張子。`.md.tmpl`はchezmoi由来の二重拡張子。
_DEFAULT_EXTENSIONS = frozenset({".md", ".md.tmpl"})

# ディレクトリ展開時にスキップするディレクトリ名。VCS管理外・自動生成・依存物を除外する。
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

# URL内の文字を検査対象から除外するためのパターン。
_URL_RE = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.\-]*://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")

# 違反種別の表示名。
_KIND_MAP = {
    "—": "em-dash(U+2014)",
    "―": "horizontal-bar(U+2015)",
    "──": "double-dash(U+2500x2)",
}

_COMMONMARK = markdown_it.MarkdownIt("commonmark")


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
    inline_blocks = _inline_block_ranges(text)
    inline_spans = _inline_code_spans(text, inline_blocks)
    searchable_text = _strip_inline_code(_mask_non_inline_blocks(text, inline_blocks), inline_spans)
    for lineno, (raw, searchable) in enumerate(zip(text.splitlines(), searchable_text.splitlines(), strict=False), start=1):
        # フェンス、コードスパン、URLを除去してからダッシュを検索する。
        searchable = _strip_urls(searchable)
        for match in _DASH_PATTERN.finditer(searchable):
            matched = match.group(0)
            kind = _KIND_MAP[matched]
            # インラインコードは同一文字数の空白で置換済みのため、除去後オフセット＝元行オフセット。
            col = match.start() + 1
            excerpt = raw if len(raw) <= _EXCERPT_LIMIT else raw[:_EXCERPT_LIMIT] + "…"
            violations.append(f'{path}:{lineno}:{col}: {kind} "{excerpt}"')

    return violations


def _inline_block_ranges(text: str) -> list[tuple[int, int]]:
    """CommonMarkのinline tokenが占める半開区間の一覧を返す。"""
    line_offsets = [0]
    for line in text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    ranges: list[tuple[int, int]] = []
    for token in _COMMONMARK.parse(text):
        if token.type != "inline" or token.map is None:
            continue
        start_line, end_line = token.map
        ranges.append((line_offsets[start_line], line_offsets[end_line]))
    return ranges


def _inline_code_spans(text: str, block_ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """各インラインブロック内のコードスパンを半開区間の一覧で返す。"""
    spans: list[tuple[int, int]] = []
    for block_start, block_end in block_ranges:
        i = block_start
        while i < block_end:
            if text[i] != "`":
                i += 1
                continue
            j = i
            while j < block_end and text[j] == "`":
                j += 1
            tick_len = j - i
            if _is_escaped(text, i):
                i = j
                continue
            close_idx = j
            while close_idx < block_end:
                if text[close_idx] != "`":
                    close_idx += 1
                    continue
                close_end = close_idx
                while close_end < block_end and text[close_end] == "`":
                    close_end += 1
                if close_end - close_idx == tick_len:
                    spans.append((i, close_end))
                    i = close_end
                    break
                close_idx = close_end
            else:
                i = j
    return spans


def _mask_non_inline_blocks(text: str, block_ranges: list[tuple[int, int]]) -> str:
    """非inlineブロックを改行以外の同長空白へ置換して返す。"""
    result = [char if char in "\r\n" else " " for char in text]
    for start, end in block_ranges:
        result[start:end] = text[start:end]
    return "".join(result)


def _strip_inline_code(text: str, spans: list[tuple[int, int]]) -> str:
    """コードスパンを改行以外の同長空白へ置換する。

    CommonMarkのブロック解析が確定した各inline token範囲内で、最大バッククォート列を
    同じ長さの最大列と対応付ける。開き候補だけ外側のバックスラッシュエスケープを判定する。
    """
    result = list(text)
    for start, end in spans:
        result[start:end] = (char if char in "\r\n" else " " for char in text[start:end])
    return "".join(result)


def _is_escaped(text: str, index: int) -> bool:
    """対象位置の文字が奇数個のバックスラッシュでエスケープされているかを返す。"""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _strip_urls(line: str) -> str:
    """行中のURLをダッシュ検査対象から除外するため、同じ長さの空白に置換する。

    スキームまたは`www.`を持たない相対リンクはURLとして除外しない。
    """
    return _URL_RE.sub(lambda m: " " * len(m.group(0)), line)


if __name__ == "__main__":
    sys.exit(main())
