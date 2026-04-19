#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Markdownの1行表示幅を半角換算で検査する独立スクリプト。

writing-standards SKILL.mdの「1行の表示幅は半角換算で127までを上限」規約を機械化する。
textlintやmarkdownlintには全角2・半角1で判定する既製ルールが無いため本スクリプトを設ける。

仕様:

- 全角文字（unicodedata.east_asian_widthが`F`/`W`/`A`）を2、それ以外を1としてカウントする
- フェンス付きコードブロック（``` または ~~~ で開閉）の内側は対象外
- 表・frontmatterを含む通常本文は閾値の対象とする
- 違反行は標準エラーへ`path:Lnn 幅=NN …先頭抜粋…`形式で列挙する
- 違反が1件以上あれば終了コード1、無ければ0
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import unicodedata

_DEFAULT_WIDTH = 127
# 先頭抜粋の最大半角換算幅。違反行を見やすく示すための切り詰め幅。
_EXCERPT_WIDTH = 60


def _char_width(c: str) -> int:
    """1文字の半角換算幅を返す。Ambiguousは全角端末を想定して2とする。"""
    return 2 if unicodedata.east_asian_width(c) in ("F", "W", "A") else 1


def _display_width(text: str) -> int:
    """半角換算の表示幅を返す。全角は2、半角は1とする。"""
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


def _is_fence(line: str) -> bool:
    """フェンス開閉行かを判定する。先頭の連続スペースは無視する。"""
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _check_file(path: pathlib.Path, max_width: int) -> list[str]:
    """1ファイルを検査し違反行のメッセージ一覧を返す。"""
    text = path.read_text(encoding="utf-8")
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
        width = _display_width(raw)
        if width > max_width:
            excerpt = _truncate(raw, _EXCERPT_WIDTH)
            violations.append(f"{path}:L{lineno} 幅={width} {excerpt}")
    return violations


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Markdownの1行表示幅（半角換算）を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象のMarkdownファイル（複数指定可）",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=_DEFAULT_WIDTH,
        help=f"半角換算の上限幅（既定: {_DEFAULT_WIDTH}）",
    )
    args = parser.parse_args()

    all_violations: list[str] = []
    for path in args.paths:
        all_violations.extend(_check_file(path, args.width))

    for line in all_violations:
        print(line, file=sys.stderr)
    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(_main())
