"""計画ファイル`## 変更内容`配下の差分ブロック走査に共通する要素を集約する共有モジュール。

`check_plan_diff_gates.py`と`check_wc_projection.py`の両者から利用する。
呼び出し側はPEP 723単独実行スクリプトのため、次のパス操作を経由してimportする。

    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from _plan_diff_parsing import (
        TEXT_FENCE_OPEN_RE,
        REDUCTION_HEADING_RE,
        is_matching_close,
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
# CommonMark仕様に従い、フェンスは3個以上のバッククォートで開始可能。
# キャプチャグループ1に開始マーカー（バッククォート列）を返す。
TEXT_FENCE_OPEN_RE = re.compile(r"^(`{3,})text\s*$")

# 汎用フェンス閉じ行の判定（言語指定なしの```閉じまたは~~~閉じ）。
# CommonMark仕様に従い、閉じフェンスは開始と同数以上の同一記号種で有効。
# キャプチャグループ1に閉じマーカー（バッククォート列またはチルダ列）を返し、
# 呼び出し側で開始マーカー種別・長さとの比較により整合を検証する。
FENCE_CLOSE_RE = re.compile(r"^(`{3,}|~{3,})\s*$")

# 汎用フェンス開始・終了判定（```pythonや~~~等、言語指定・記号種別を問わない）。
# H2見出し境界判定でフェンス内の`## `様の行を除外する用途に用いる。
FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# 縮減対象小見出しの判定（`#### 縮減対象`および`#### 縮減対象（xxx）`）。
REDUCTION_HEADING_RE = re.compile(r"^####\s*縮減対象")

# frontmatter区間（`^---$`〜`^---$`区間）向けサブラベル行の完全一致判定。
# `plan-file-diff-labels.md`「frontmatter変更用サブラベル」節が定める4種
# （現行・追記・置換後・削除根拠）のいずれかに完全一致する行を検出し、
# キャプチャグループ1にラベル種別トークン（角括弧・「（frontmatter）」を除いた文字列）を返す。
FRONTMATTER_LABEL_RE = re.compile(r"^\[(現行|追記|置換後|削除根拠)（frontmatter）\]$")

# `#### 縮減対象（<ファイル名>）`H4見出しからファイル名部分を抽出する。
# 全角丸括弧・半角丸括弧の双方に対応する。
REDUCTION_HEADING_WITH_FILE_RE = re.compile(r"^####\s*縮減対象[（(]([^）)]+)[）)]")


def is_matching_close(open_marker: str, line: str) -> bool:
    """`line`が`open_marker`で開いたフェンスの閉じ行として整合するかを判定する。

    CommonMark仕様に従い、閉じフェンスは開始と同数以上の同一記号種
    （バッククォート列またはチルダ列）で有効となる。
    """
    m = FENCE_CLOSE_RE.match(line)
    if m is None:
        return False
    close_marker = m.group(1)
    return close_marker[0] == open_marker[0] and len(close_marker) >= len(open_marker)


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
            elif is_matching_close(fence_marker, line):
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


def extract_h3_section_with_offset(text: str, heading: str) -> tuple[str, int]:
    """指定H3見出し直後から同階層以上の次見出し直前までを抽出する。

    引数`heading`はH3見出しの先頭マーカーを含めた完全一致文字列（例:`### エージェント判断`）で指定する。
    戻り値は`(本文, 開始行番号1始まり)`。指定見出しが本文中に存在しない場合は`("", 0)`を返す。
    フェンス内行は`iter_non_fenced_lines`で除外し、フェンス内の`### `文字列を境界誤検出しない。
    `check_plan_diff_gates.py`の`_extract_judgment_section_body`・`_has_responsibility_diff_table`
    等のH3走査系関数から共通利用する。H2境界前提の`extract_section_with_offset`と混同しない。
    """
    lines = text.splitlines()
    heading_line = heading.strip()
    body_lines: list[str] = []
    start_line = 0
    in_section = False
    for idx, line in iter_non_fenced_lines(lines):
        stripped = line.strip()
        if not in_section:
            if stripped == heading_line:
                in_section = True
                start_line = idx + 1
            continue
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            break
        body_lines.append(line)
    return "\n".join(body_lines), start_line


def iter_reduction_headings(section: str) -> Iterator[str]:
    """`section`本文中の`#### 縮減対象（<ファイル名>）`H4見出しからファイル名を順に返す。

    `check_wc_projection.py`と`posttooluse.py`の双方から利用可能な共通ヘルパー。
    フェンス内の`#### `様の行は`iter_non_fenced_lines`で除外する。
    返却文字列はbasename・相対パス末尾・basename含有修飾名（例: `agent-standards SKILL.md`）の
    いずれの表記も加工せず透過的に返す。表記形式ごとの突合ロジックは呼び出し側の責務とする。
    """
    lines = section.splitlines()
    for _idx, line in iter_non_fenced_lines(lines):
        m = REDUCTION_HEADING_WITH_FILE_RE.match(line.strip())
        if m:
            yield m.group(1).strip()
