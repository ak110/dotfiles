#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイルの`#### 廃止・改名対象一覧`H4に列挙した識別子の残存参照を機械照合する。

`agent-toolkit:plan-mode`工程7前のメイン側セルフチェックから呼び出される。
計画ファイルが廃止・改名するスキル名・パス・関数名・変数名等の識別子について、
リポジトリ横断`grep -rn`のヒットファイル集合と`## 変更内容`「対象ファイル一覧」の
差集合を検出する。差集合が非空の場合、計画側の対象ファイル一覧に未反映の残存参照が
存在することを意味するため違反として報告する。

`#### 廃止・改名対象一覧`H4が計画ファイルに存在しない場合、または配下に識別子が
1件も列挙されていない場合は、廃止・改名対象なしとして即座にexit 0で終了する。

出力形式は兄弟スクリプト`check_line_ref.py`と揃える（stderrへメッセージ、違反ありでexit 1）。
共通要素は`_plan_diff_parsing.py`へ集約済みであり`extract_section_with_offset`をimportで参照する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

# 共通モジュール読み込みのため本ファイルと同一ディレクトリを`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from _plan_diff_parsing import extract_section_with_offset, iter_non_fenced_lines  # noqa: E402

# pylint: enable=wrong-import-position


# `#### 廃止・改名対象一覧`H4見出し（`plan-file-guidelines.md`が定める機械可読形式）。
_DEPRECATED_HEADING = "#### 廃止・改名対象一覧"

# 任意レベルの見出し行（H4節の終端判定に用いる）。
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s")

# `- \`<identifier>\``形式のリスト項目から識別子を抽出するパターン。
_IDENTIFIER_ITEM_RE = re.compile(r"^-\s*`([^`]+)`")

# `## 変更内容`直下の対象ファイル一覧チェックボックス項目からパスのみを抽出するパターン。
# 見込み行数の記載有無・新設/既存の別を問わず対象パスを収集する。
_CHECKBOX_PATH_RE = re.compile(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\s（(]+)`?")

# リポジトリ横断grepの対象拡張子。agent-toolkitで管理する主要テキスト形式を網羅する。
_TARGET_EXTENSION_PATTERNS: tuple[str, ...] = (
    "*.md",
    "*.md.tmpl",
    "*.py",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.sh",
    "*.ps1",
    "*.cmd",
)


def main() -> int:
    """廃止・改名識別子の残存参照照合のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="計画ファイルの廃止・改名対象識別子について、"
        "リポジトリ横断grepのヒットファイル集合と対象ファイル一覧の差集合を検査する。",
    )
    parser.add_argument(
        "plan_paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象の計画ファイル（複数指定可）",
    )
    args = parser.parse_args()

    repo_root = _find_repo_root(pathlib.Path.cwd())
    total_violations = 0
    for plan_path in args.plan_paths:
        total_violations += _check_plan(plan_path, repo_root)
    return 1 if total_violations > 0 else 0


def _find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """`start`から`.git`を遡り探索してリポジトリルートを解決する。

    見つからない場合は`start`自体をリポジトリルートとみなす
    （CLI起動時のカレントディレクトリへのフォールバック）。
    """
    resolved = start.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved


def _check_plan(plan_path: pathlib.Path, repo_root: pathlib.Path) -> int:
    """1計画ファイルを検査し、違反件数（未反映の残存参照ヒット件数）を返す。"""
    text = plan_path.read_text(encoding="utf-8")

    deprecated_section = _extract_deprecated_section(text)
    if deprecated_section is None:
        print(f"{plan_path}: `{_DEPRECATED_HEADING}`なし。廃止・改名対象なし", file=sys.stderr)
        return 0

    identifiers = _extract_identifiers(deprecated_section)
    if not identifiers:
        print(f"{plan_path}: {_DEPRECATED_HEADING}は空。廃止・改名対象なし", file=sys.stderr)
        return 0

    changes_section, _offset = extract_section_with_offset(text, "## 変更内容")
    known_paths = _collect_known_paths(changes_section) if changes_section is not None else frozenset()

    violations = 0
    for identifier in identifiers:
        for rel_path, lineno, content in _grep_identifier(repo_root, identifier):
            if rel_path in known_paths:
                continue
            print(
                f"{plan_path}: `{identifier}`の残存参照が対象ファイル一覧に未反映: {rel_path}:{lineno}: {content.strip()}",
                file=sys.stderr,
            )
            violations += 1
    return violations


def _extract_deprecated_section(text: str) -> str | None:
    """`#### 廃止・改名対象一覧`H4見出し直後から次の見出し直前までの本文を返す。

    見出しが見つからない場合は`None`を返す。`extract_section_with_offset`はH2見出し
    専用のため、任意レベルの見出しに対応する本関数を別途用意する。
    """
    lines = text.splitlines()
    start: int | None = None
    for idx, line in iter_non_fenced_lines(lines):
        if line.strip() == _DEPRECATED_HEADING:
            start = idx + 1
            break
    if start is None:
        return None

    end = len(lines)
    for idx, line in iter_non_fenced_lines(lines, start):
        if _ANY_HEADING_RE.match(line):
            end = idx
            break
    return "\n".join(lines[start:end])


def _extract_identifiers(section: str) -> list[str]:
    r"""`- \\`<identifier>\\``形式のリスト項目から識別子一覧を順に抽出する。"""
    identifiers: list[str] = []
    for _idx, line in iter_non_fenced_lines(section.splitlines()):
        m = _IDENTIFIER_ITEM_RE.match(line.strip())
        if m:
            identifiers.append(m.group(1))
    return identifiers


def _collect_known_paths(section: str) -> frozenset[str]:
    """`## 変更内容`本文の対象ファイル一覧チェックボックス項目から既知パス集合を構築する。"""
    known_paths: set[str] = set()
    for line in section.splitlines():
        m = _CHECKBOX_PATH_RE.match(line)
        if m:
            known_paths.add(m.group("path"))
    return frozenset(known_paths)


def _grep_identifier(repo_root: pathlib.Path, identifier: str) -> list[tuple[str, str, str]]:
    """`identifier`をリポジトリルート起点で対象拡張子へ`grep -rnF`し、(相対パス, 行番号, 該当行)一覧を返す。

    grepの終了コード1（無ヒット）は正常系として空リストを返す。
    終了コード2以上（引数不正等）は照合対象外として空リストを返す。
    """
    include_args = [f"--include={pattern}" for pattern in _TARGET_EXTENSION_PATTERNS]
    result = subprocess.run(
        ["grep", "-rnF", "--exclude-dir=.git", *include_args, identifier, str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    hits: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        abs_path, lineno, content = parts
        rel_path = pathlib.Path(abs_path).relative_to(repo_root).as_posix()
        hits.append((rel_path, lineno, content))
    return hits


if __name__ == "__main__":
    sys.exit(main())
