#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル本文の自己参照曖昧候補・禁止形式候補・H1改称差分を検出する独立スクリプト。

計画ファイル`## 変更内容`H3配下の`text`コードブロック内文面を対象に検査する。
`agent-toolkit:plan-file-creator`の整合性チェック時のセルフチェックから呼び出される。
検出パターンは次の3種。
- 自己参照曖昧候補: 「本節のバレット項目」等の対象が計画ファイル内容と実対象文書のどちらを指すか
  判別できない表現。追記文面案内で頻出し、実装時に指示範囲を取り違える原因となる。
- 禁止形式候補: `Xを根拠にYしない`型の否定規定。全称否定形（`いかなる〜があってもYしない`）で
  書くべきという`agent-toolkit/rules/04-styles.md`規範の機械化。
- H1改称差分: `## 変更履歴`または`## 変更内容`のH3見出しがH1（`# タイトル`）の
  改称・変更・拡張を宣言する場合、対応するH3配下の`text`コードブロックに実際に
  H1行の変更差分が含まれているかを検証する。宣言と実差分の欠落を検出する。

出力形式は兄弟スクリプト`check_line_ref.py`と揃える（stderrへ`<行番号>: <カテゴリ>: <該当行>`、違反ありでexit 1）。
`## 変更内容`H2外・`text`コードブロック外の記述は誤検出しない。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# 抜粋の最大文字数。違反行を見やすく示す切り詰め幅。
_EXCERPT_LIMIT = 80

# 自己参照曖昧候補の検出パターン。`本節|本項|本規則|同節`に続く「のバレット項目」・
# 「本節の点検項目」・「本節の全」を検出する。
_SELF_REF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:本節|本項|本規則|同節)のバレット項目"),
    re.compile(r"本節の点検項目"),
    re.compile(r"本節の全"),
)

# 禁止形式候補の検出パターン。`〜を根拠に(しない|用いない|判定しない)`型。
_FORBIDDEN_FORM_PATTERN = re.compile(r"を根拠に(?:しない|用いない|判定しない)")

# H3見出し検出パターン。
_H3_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")

# H2見出し検出パターン。
_H2_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

# `text`コードブロック開始フェンス。
_TEXT_FENCE_OPEN_RE = re.compile(r"^```text\s*$")

# 汎用フェンス開閉判定（言語指定・記号種別を問わず捕捉する）。
_FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# H1変更宣言キーワード。改称・変更・拡張のいずれかをH3見出し文言に含む場合を対象にする。
_H1_CHANGE_KEYWORDS = ("改称", "変更", "拡張")

# H3見出しがH1指示（`# `トークン）を含むかを判定するパターン。
# 具体的にはH3タイトル内に`# タイトル`様のトークンが埋め込まれているか、
# または`H1`という語自体を含む場合をH1宣言と判定する。
_H1_REFERENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[^#])#\s*[^#\s]"),  # `# タイトル`様のトークン
    re.compile(r"H1"),
    re.compile(r"タイトル"),
)

# text コードブロック内でH1行と判定するパターン。行頭`# `かつ`##`ではない。
_H1_LINE_RE = re.compile(r"^\s*[+\-]?\s*#\s+[^#]")

# 対象H2見出し。
_TARGET_H2_HEADINGS = frozenset({"変更内容", "変更履歴"})


def main() -> int:
    """自己参照検査のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="計画ファイル本文の自己参照曖昧候補・禁止形式候補・H1改称差分を検査する。",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象の計画ファイル（複数指定可）",
    )
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
    """1計画ファイルを検査して違反行のメッセージ一覧を返す。

    `## 変更内容`H2配下のH3見出しごとに`text`コードブロックを収集し、
    パターン1（自己参照曖昧候補）・パターン2（禁止形式候補）を各ブロック内文面へ適用する。
    パターン3（H1改称差分）は`## 変更内容`または`## 変更履歴`のH3見出しに
    改称・変更・拡張とH1指示語を併記する場合、配下ブロックのH1行差分の存在を検証する。
    """
    violations: list[str] = []
    lines = text.splitlines()
    n = len(lines)

    # H2状態・H3状態を追跡しながら走査する。
    current_h2: str | None = None
    current_h3: str | None = None
    current_h3_lineno: int | None = None
    # 現H3配下で観測したH1行の有無（パターン3判定用）。
    h3_has_h1_change = False
    # 現H3見出しでH1変更宣言があるか（パターン3判定用）。
    h3_declares_h1_change = False

    i = 0
    while i < n:
        raw = lines[i]

        # H2見出し切り替え。
        m_h2 = _H2_HEADING_RE.match(raw)
        if m_h2:
            # 直前H3のパターン3判定を確定する。
            violations.extend(
                _finalize_h3_pattern3(path, current_h3, current_h3_lineno, h3_declares_h1_change, h3_has_h1_change)
            )
            current_h2 = m_h2.group(1).strip()
            current_h3 = None
            current_h3_lineno = None
            h3_has_h1_change = False
            h3_declares_h1_change = False
            i += 1
            continue

        # H3見出し切り替え。
        m_h3 = _H3_HEADING_RE.match(raw)
        if m_h3:
            violations.extend(
                _finalize_h3_pattern3(path, current_h3, current_h3_lineno, h3_declares_h1_change, h3_has_h1_change)
            )
            h3_title = m_h3.group(1).strip()
            current_h3 = h3_title
            current_h3_lineno = i + 1
            h3_has_h1_change = False
            h3_declares_h1_change = _declares_h1_change(h3_title) if current_h2 in _TARGET_H2_HEADINGS else False
            i += 1
            continue

        # `## 変更内容`H2外・H3外はスキップする。
        if current_h2 != "変更内容" or current_h3 is None:
            i += 1
            continue

        # `text`フェンスブロックのみ検査対象とする。
        if _TEXT_FENCE_OPEN_RE.match(raw):
            block_end = _find_fence_close(lines, i + 1)
            for j in range(i + 1, block_end):
                block_line = lines[j]
                lineno = j + 1
                # パターン1: 自己参照曖昧候補。
                for pat in _SELF_REF_PATTERNS:
                    if pat.search(block_line):
                        excerpt = _excerpt(block_line)
                        violations.append(f"{path}:{lineno}: self-ref: {excerpt}")
                        break
                # パターン2: 禁止形式候補。
                if _FORBIDDEN_FORM_PATTERN.search(block_line):
                    excerpt = _excerpt(block_line)
                    violations.append(f"{path}:{lineno}: forbidden-form: {excerpt}")
                # パターン3集計: H1行の変更差分。
                if _H1_LINE_RE.match(block_line):
                    h3_has_h1_change = True
            i = block_end + 1
            continue

        # 非フェンス行はスキップ。
        i += 1

    # ファイル末尾に残るH3のパターン3判定を確定する。
    violations.extend(_finalize_h3_pattern3(path, current_h3, current_h3_lineno, h3_declares_h1_change, h3_has_h1_change))
    return violations


def _find_fence_close(lines: list[str], start: int) -> int:
    """`start`以降で最初のフェンス閉じ行のインデックスを返す。見つからない場合は末尾インデックスを返す。"""
    for j in range(start, len(lines)):
        if _FENCE_RE.match(lines[j]):
            return j
    return len(lines) - 1


def _declares_h1_change(h3_heading: str) -> bool:
    """H3見出しがH1変更宣言（改称・変更・拡張＋H1指示語）かを判定する。"""
    if not any(kw in h3_heading for kw in _H1_CHANGE_KEYWORDS):
        return False
    return any(pat.search(h3_heading) for pat in _H1_REFERENCE_PATTERNS)


def _finalize_h3_pattern3(
    path: pathlib.Path,
    h3_title: str | None,
    h3_lineno: int | None,
    declares: bool,
    has_h1_change: bool,
) -> list[str]:
    """H3切り替え・ファイル末尾時にパターン3の違反判定を確定する。"""
    if h3_title is None or h3_lineno is None:
        return []
    if declares and not has_h1_change:
        return [
            f"{path}:{h3_lineno}: h1-change-missing: H3「{h3_title}」がH1変更を宣言するが配下`text`ブロックに`# `行差分が無い"
        ]
    return []


def _excerpt(line: str) -> str:
    """違反行の抜粋を切り詰めて返す。"""
    stripped = line.rstrip()
    return stripped if len(stripped) <= _EXCERPT_LIMIT else stripped[:_EXCERPT_LIMIT] + "…"


if __name__ == "__main__":
    sys.exit(main())
