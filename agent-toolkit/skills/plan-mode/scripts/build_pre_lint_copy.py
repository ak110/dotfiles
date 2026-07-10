#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイルから事前検査用コピーを生成する。

`## 背景`H2見出し配下のフェンス付きコードブロックのみを除外し、
除去痕として生じた連続空行（3行以上）を1個の空行へ正規化して出力する。

`## 背景`以外の節（`## 変更内容`等）に含まれるフェンスは保持する。
`agent-toolkit:plan-mode`の事前機械検査を`## 背景`配下の原文転記領域から発火させないための前処理として用いる。

CLI: `build_pre_lint_copy.py <plan_file> <output_file>`
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# 共通モジュール読み込みのため本ファイルと同一ディレクトリを`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from _plan_diff_parsing import FENCE_RE, is_matching_close  # noqa: E402

# pylint: enable=wrong-import-position

# 3行以上の連続空行を1個の空行へ正規化する。
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

_BACKGROUND_HEADING = "## 背景"


def build_pre_lint_copy(text: str) -> str:
    """`text`から`## 背景`配下のフェンス付きコードブロックを除外し、連続空行を正規化した文字列を返す。

    `## 背景`H2見出しに到達してから、次のH2見出しに到達するまでの領域を対象範囲とする。
    範囲内で開いたフェンスは範囲内で閉じたと見なして除去する。
    範囲外（`## 背景`以外の節）はそのまま保持する。
    """
    lines = text.splitlines()
    out_lines: list[str] = []
    in_background = False
    in_fence = False
    fence_marker = ""

    for raw in lines:
        stripped = raw.strip()
        # H2見出し境界判定はフェンス外でのみ行う。
        if not in_fence and stripped.startswith("## "):
            in_background = stripped == _BACKGROUND_HEADING
            out_lines.append(raw)
            continue

        if in_background:
            m_fence = FENCE_RE.match(raw)
            if m_fence:
                marker = m_fence.group(2)
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                    continue
                if is_matching_close(fence_marker, raw):
                    in_fence = False
                    fence_marker = ""
                    continue
                # 同種フェンスの開閉不整合時は開いたままとして扱う。
                continue
            if in_fence:
                continue
            out_lines.append(raw)
            continue

        out_lines.append(raw)

    result = "\n".join(out_lines)
    # 元テキストが末尾改行を持つ場合は保持する。
    if text.endswith("\n"):
        result += "\n"
    # 除去痕として生じた連続空行を1個の空行へ正規化する。
    result = _MULTI_BLANK_RE.sub("\n\n", result)
    return result


def main() -> int:
    """CLIエントリーポイント。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_file", type=pathlib.Path, help="入力計画ファイルパス")
    parser.add_argument("output_file", type=pathlib.Path, help="出力先ファイルパス")
    args = parser.parse_args()

    text = args.plan_file.read_text(encoding="utf-8")
    result = build_pre_lint_copy(text)
    args.output_file.write_text(result, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
