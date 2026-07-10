"""計画ファイル`## 変更内容`配下の差分ブロック走査に共通する要素を集約する共有モジュール。

`check_plan_diff_gates.py`と`check_wc_projection.py`の両者から利用する。
呼び出し側はPEP 723単独実行スクリプトのため、次のパス操作を経由してimportする。

    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from _plan_diff_parsing import (
        TEXT_FENCE_OPEN_RE,
        FENCE_CLOSE_RE,
        FENCE_RE,
        REDUCTION_HEADING_RE,
        iter_non_fenced_lines,
        extract_section_with_offset,
    )

集約対象は両スクリプト間で完全一致する要素に限る。
意味論的差異のある要素（`_H3_RE`のグループ名の差・
`_CURRENT_LABEL_TOKEN`／`_REPLACEMENT_LABEL_TOKEN`の角括弧の有無など）は各スクリプト固有として温存する。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# `text`フェンス開始行の判定。言語指定が`text`のフェンスのみを対象とする。
TEXT_FENCE_OPEN_RE = re.compile(r"^```text\s*$")

# 汎用フェンス閉じ行の判定（言語指定なしの```閉じ）。
FENCE_CLOSE_RE = re.compile(r"^```\s*$")

# 汎用フェンス開始・終了判定（```pythonや~~~等、言語指定・記号種別を問わない）。
# H2見出し境界判定でフェンス内の`## `様の行を除外する用途に用いる。
FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# 縮減対象小見出しの判定（`#### 縮減対象`および`#### 縮減対象（xxx）`）。
REDUCTION_HEADING_RE = re.compile(r"^####\s*縮減対象")


def iter_non_fenced_lines(lines: list[str], start: int = 0) -> Iterator[tuple[int, str]]:
    """```・~~~フェンス内の行を除外し、`(行番号, 行内容)`を順に返す。

    フェンス開閉状態を跨いで呼び出す用途は想定せず、
    呼び出しごとに`start`から新規にフェンス状態を追跡する。
    """
    in_fence = False
    fence_marker = ""
    for idx in range(start, len(lines)):
        line = lines[idx]
        m_fence = FENCE_RE.match(line)
        if m_fence:
            marker = m_fence.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        yield idx, line


def extract_section_with_offset(text: str, heading: str) -> tuple[str | None, int]:
    """指定H2見出し直後から次のH2見出し直前までの本文と、本文の開始行番号（1始まり）を返す。

    見出しが見つからない場合は`(None, 0)`を返す。
    フェンス内に`## `始まりの行が含まれる場合の誤終端を避けるため、
    `iter_non_fenced_lines`でフェンス内行を除外してから見出し判定する。
    """
    lines = text.splitlines()
    start: int | None = None
    for idx, line in iter_non_fenced_lines(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        return None, 0

    end = len(lines)
    for idx, line in iter_non_fenced_lines(lines, start):
        if line.startswith("## ") and line.strip() != heading:
            end = idx
            break
    return "\n".join(lines[start:end]), start
