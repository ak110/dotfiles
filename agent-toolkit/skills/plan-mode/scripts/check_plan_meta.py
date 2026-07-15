#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイルの`## 背景`配下`### 計画メタ情報`H3と起動経路・対象リポジトリ2項目の存在を検査する独立スクリプト。

`agent-toolkit:plan-mode`工程7のメイン側セルフチェックおよび`check_plan_file.py`統合ランナーから呼び出される。
`### 計画メタ情報`H3が`## 背景`直下に存在するか、配下に`- 起動経路:`・`- 対象リポジトリ:`で始まる
箇条書き行が1件ずつ存在するかを検査する。欠落時は重大指摘としてstderrへ出力しexit 1で終了する。
既存計画ファイルへの遡及適用はしない。対象は新規作成する計画ファイルに限る。運用は呼び出し元が判断する。
H3タイトル文言は`plan-file-guidelines.md`「背景（`## 背景`）」節・`sample.md`・
`integrity-checks.md`・`bugfix-process.md`のH3構成と同期させる（frontmatter同期注記）。

出力形式は兄弟スクリプト`check_line_ref.py`と揃える
（stderrへ`<path>:<行番号>: <カテゴリ>: <欠落内容の説明文>`、違反ありでexit 1）。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

_H2_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")
_META_H3_TITLE = "計画メタ情報"
_LAUNCH_ROUTE_RE = re.compile(r"^-\s*起動経路\s*[:：]")
_TARGET_REPO_RE = re.compile(r"^-\s*対象リポジトリ\s*[:：]")


def main() -> int:
    """計画メタ情報検査のエントリポイント。"""
    parser = argparse.ArgumentParser(description="計画ファイルの`### 計画メタ情報`H3と必須2項目を検査する。")
    parser.add_argument("paths", nargs="+", type=pathlib.Path, help="検査対象の計画ファイル（複数指定可）")
    args = parser.parse_args()

    all_violations: list[str] = []
    for path in args.paths:
        text = _read_text_or_none(path)
        if text is None:
            print(f"{path}: 計画ファイルの読み込みに失敗", file=sys.stderr)
            all_violations.append(f"{path}: read-error")
            continue
        all_violations.extend(_check_file(path, text))

    for line in all_violations:
        print(line, file=sys.stderr)
    return 1 if all_violations else 0


def _read_text_or_none(path: pathlib.Path) -> str | None:
    """ファイルを読み込む。読み込み失敗時は`None`を返す。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _check_file(path: pathlib.Path, text: str) -> list[str]:
    """1計画ファイルを検査し、`## 背景`配下`### 計画メタ情報`H3と必須2項目の欠落を報告する。"""
    lines = text.splitlines()
    bg_start, bg_end = _find_section(lines, _H2_HEADING_RE, "背景", 0, len(lines))
    if bg_start is None:
        return [f"{path}:1: plan-meta-missing: `## 背景`セクションが存在しない"]

    meta_start, meta_end = _find_section(lines, _H3_HEADING_RE, _META_H3_TITLE, bg_start + 1, bg_end)
    if meta_start is None:
        return [f"{path}:{bg_start + 1}: plan-meta-missing: `## 背景`配下に`### 計画メタ情報`H3が存在しない"]

    violations: list[str] = []
    section_lines = lines[meta_start:meta_end]
    if not any(_LAUNCH_ROUTE_RE.match(line) for line in section_lines):
        violations.append(f"{path}:{meta_start + 1}: plan-meta-missing: `- 起動経路:`行が存在しない")
    if not any(_TARGET_REPO_RE.match(line) for line in section_lines):
        violations.append(f"{path}:{meta_start + 1}: plan-meta-missing: `- 対象リポジトリ:`行が存在しない")
    return violations


def _find_section(
    lines: list[str], heading_re: re.Pattern[str], title: str, search_start: int, search_end: int
) -> tuple[int | None, int]:
    """`search_start`以降・`search_end`未満の範囲で`title`に一致する見出し行の開始・終了位置を返す。

    終了位置は同レベル以上の次の見出し行、または`search_end`のいずれか早い方。
    """
    start: int | None = None
    end = search_end
    for i in range(search_start, search_end):
        m = heading_re.match(lines[i])
        if m is None:
            continue
        if start is None:
            if m.group(1).strip() == title:
                start = i
            continue
        end = i
        break
    return start, end


if __name__ == "__main__":
    sys.exit(main())
