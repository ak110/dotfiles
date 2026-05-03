#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""口語的な日本語表現の混入を検査する独立スクリプト。

writing-standards SKILL.mdの「書き言葉・フォーマルな表現を厳守する」規約を機械化する。
agent-toolkitプラグイン同梱の辞書ファイル（`agent-toolkit/scripts/_colloquial_words.txt`
と `_colloquial_words_allow.txt`）を共通ロジック経由で読み込み、
対象ファイルから検出された口語表現を列挙する。

仕様:

- 検出は `agent-toolkit/scripts/_colloquial_check.py` の `scan_text` を使う
- 引数にはファイルパスとディレクトリパスの両方を指定可能。
  ディレクトリの場合は対象拡張子（`.md` `.py` `.txt` `.yaml` `.yml` `.toml`）を
  再帰的に走査し、`.git`・`.venv`・`node_modules`・`__pycache__`・各種キャッシュ
  ディレクトリは除外する
- 違反行は標準エラーへ `path:Lnn:Cnn [match] …抜粋…` 形式で列挙する
- 違反が1件以上あれば終了コード1、無ければ0
- 検出辞書をエージェントのコンテキストへ持ち込まない設計のため、
  本スクリプトの実行結果（stderr）を読む際は注意する
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# agent-toolkit/scripts を sys.path に追加し、共通モジュールを読み込む。
# 本スクリプトは agent-toolkit/skills/writing-standards/scripts/ 配下に置かれる前提。
_AGENT_TOOLKIT_SCRIPTS = pathlib.Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(_AGENT_TOOLKIT_SCRIPTS))
import _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position,import-error

# 抜粋の最大文字数。違反行を見やすく示すための切り詰め幅。
_EXCERPT_LIMIT = 100

# ディレクトリ展開時に走査する拡張子。日本語が含まれうるテキストファイルを対象とする。
_DEFAULT_EXTENSIONS = frozenset({".md", ".py", ".txt", ".yaml", ".yml", ".toml"})

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

# 検査対象から自動的に外す辞書ファイル本体（自身を検査するとほぼ全行マッチするため）。
_DICT_FILES = frozenset({_colloquial_check.DENY_PATH.resolve(), _colloquial_check.ALLOW_PATH.resolve()})


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="口語的な日本語表現の混入を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のファイルまたはディレクトリ（複数指定可）",
    )
    args = parser.parse_args()

    deny_patterns = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
    allow_patterns = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)
    if not deny_patterns:
        # 辞書未配置・空でも安全側に通過させる
        return 0

    targets = _expand_paths(args.paths)
    total = 0
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, col, match_str, snippet in _colloquial_check.scan_text(text, deny_patterns, allow_patterns):
            excerpt = snippet if len(snippet) <= _EXCERPT_LIMIT else snippet[:_EXCERPT_LIMIT] + "…"
            print(f"{path}:L{line_no}:C{col} [{match_str}] {excerpt}", file=sys.stderr)
            total += 1
    if total:
        print(
            f"colloquial-check: {total} colloquial expression(s) detected. Rewrite using formal written-style Japanese.",
            file=sys.stderr,
        )
        return 1
    return 0


def _expand_paths(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    """ファイル/ディレクトリ混在の入力を、検査対象ファイルの一覧へ展開する。

    ディレクトリは再帰的に対象拡張子のファイルを収集する。
    `_EXCLUDED_DIRS` 配下と辞書ファイル自身は除外する。
    順序の安定性のため、ディレクトリ展開分は path 順に並べる。
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
                if any(part in _EXCLUDED_DIRS for part in sub.parts):
                    continue
                if sub.suffix.lower() not in _DEFAULT_EXTENSIONS:
                    continue
                _add(expanded, seen, sub)
    return expanded


def _add(out: list[pathlib.Path], seen: set[pathlib.Path], path: pathlib.Path) -> None:
    """重複・辞書ファイル本体を除いて出力リストへ追加する。"""
    resolved = path.resolve()
    if resolved in _DICT_FILES or resolved in seen:
        return
    seen.add(resolved)
    out.append(path)


if __name__ == "__main__":
    sys.exit(_main())
