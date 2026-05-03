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
import _colloquial_check  # noqa: E402  # pylint: disable=wrong-import-position

# 抜粋の最大文字数。違反行を見やすく示すための切り詰め幅。
_EXCERPT_LIMIT = 100


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="口語的な日本語表現の混入を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のファイル（複数指定可）",
    )
    args = parser.parse_args()

    deny_patterns = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
    allow_patterns = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)
    if not deny_patterns:
        # 辞書未配置・空でも安全側に通過させる
        return 0

    total = 0
    for path in args.paths:
        if not path.is_file():
            continue
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


if __name__ == "__main__":
    sys.exit(_main())
