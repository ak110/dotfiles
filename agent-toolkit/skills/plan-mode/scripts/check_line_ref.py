#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル本文の行番号への参照を検査する独立スクリプト。

plan-mode配下 plan-file-guidelines.mdの絶対数値の直書き回避規定
（対象は行番号への参照全般）を機械化する。
検出対象は`Lxx`・`Lxx-yy`形式・`xx行目`形式・`xx-yy行`形式・`xxからyy行`形式の行番号参照とし、
`Lxx`形式はASCII英数字への否定先読み・後読みにより`HTML5`・`URL2`等の識別子内包を誤検出しない。
除外条件はフェンス付きコードブロック内・インラインコード内・
`## 調査結果`H2セクション配下かつ同一行に`<!-- line-ref-ok -->`コメントを持つ行。
`## 調査結果`外の節ではマーカー付与に関わらず違反として報告する。

本ファイルは兄弟スクリプト
`agent-toolkit/skills/writing-standards/scripts/check_dash.py`および
`agent-toolkit/skills/writing-standards/scripts/check_line_width.py`と共通のヘルパー
（`_expand_paths`・`_add`・`_strip_inline_code`・`_FENCE_RE`等）を意図的に複製している。
PEP 723単独実行スクリプト制約下で外部モジュールへ切り出せないため。
共通処理へ修正・バグ修正を加える場合は兄弟スクリプトも同一計画内で同時修正する。
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

# 検出対象のパターン集合。`agent-toolkit/scripts/pretooluse.py`の`_LINE_NUMBER_PATTERNS`と同範囲。
# `L\d+`形式はASCII英数字への否定先読み・後読みで`HTML5`・`URL2`等の識別子内包を除外する。
_LINE_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z0-9])L\d+(?:-\d+)?(?![A-Za-z0-9])"),
    re.compile(r"\d+行目"),
    re.compile(r"\d+\s*-\s*\d+\s*行"),
    re.compile(r"\d+から\d+行"),
)

# フェンス開始の最小バッククォート/チルダ数。
_FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# H2見出し検出パターン。見出し語の先頭単語のみ取得する。
_H2_HEADING_RE = re.compile(r"^##\s+(\S+)")

# 個別抑止マーカーの有効範囲となるH2見出し名。
_INVESTIGATION_HEADING = "調査結果"

# 同一行での個別抑止マーカー。
_LINE_ALLOW_MARKER = "<!-- line-ref-ok -->"


def main() -> int:
    """行番号への参照検査のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="計画ファイル本文の行番号参照を検査する。",
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
    in_investigation = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        # フェンス開閉判定。バッククォート3個以上またはチルダ3個以上。
        m_fence = _FENCE_RE.match(raw)
        if m_fence:
            marker = m_fence.group(2)
            if not in_fence:
                in_fence = True
                # 開始フェンスの全長を保持し、閉じ判定に使う。
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
            continue

        if in_fence:
            continue

        # H2見出し判定。`## 調査結果`配下かどうかの状態を更新する。
        m_h2 = _H2_HEADING_RE.match(raw)
        if m_h2:
            in_investigation = m_h2.group(1) == _INVESTIGATION_HEADING
            continue

        # `## 調査結果`配下かつ同一行に個別抑止マーカーがあれば検査をスキップする。
        if in_investigation and _LINE_ALLOW_MARKER in raw:
            continue

        # インラインコードを除去してから行番号参照を検索する。
        searchable = _strip_inline_code(raw)
        for pattern in _LINE_REF_PATTERNS:
            for match in pattern.finditer(searchable):
                # インラインコードは同一文字数の空白で置換済みのため、除去後オフセット＝元行オフセット。
                col = match.start() + 1
                excerpt = raw if len(raw) <= _EXCERPT_LIMIT else raw[:_EXCERPT_LIMIT] + "…"
                violations.append(f'{path}:{lineno}:{col}: line-ref "{excerpt}"')

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
