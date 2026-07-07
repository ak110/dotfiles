#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル内の[現行]/[置換後]対比ブロックを機械適用し、wc -l実測値と見込み行数の乖離を検出する。

`agent-toolkit:plan-mode`工程7のメイン側セルフチェックから呼び出される。
アルゴリズムの詳細は`_parse_plan_file`・`_extract_diff_blocks`・`_check_one_file`各関数のdocstringを参照する。
出力形式は兄弟スクリプト`check_line_ref.py`と揃える（stderrへメッセージ、違反ありでexit 1）。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# 見込み行数との許容乖離幅（行）。この幅を超えた差分のみ違反として報告する。
_ALLOWED_DRIFT = 2

# `## 変更内容`直下の対象ファイル一覧チェックボックス項目。
# 既存ファイル`- [ ] path（現行N行, 見込みM行）`と新設ファイル`- [ ] path（新設, 見込みM行）`の
# 両形式から相対パスと見込み行数を抽出する。
_CHECKBOX_RE = re.compile(
    r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\n]+?)`?\s*"
    r"[（(](?:現行(?:\d+)行|新設),\s*見込み(?P<projected>\d+)行[）)]"
)

# 対象ファイル一覧の全チェックボックス項目（見込み行数の有無を問わない）。
# 既知パス集合の構築に用いる。H3見出しのパス候補と突合してファイル判定する。
_CHECKBOX_PATH_RE = re.compile(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\s（(]+)`?")

# `### <相対パス>`H3見出し。バッククォート付き・「（新設）」等の注記付きの双方に対応する。
_H3_RE = re.compile(r"^###\s+`?(?P<path>[^`\s（(]+)`?")

# [現行]/[置換後]対比ブロックの開始フェンス。```textのみを対象とする（```pythonや```bash等の
# 骨格提示ブロックは対比対象外のため誤検出しない）。
_TEXT_FENCE_OPEN_RE = re.compile(r"^```text\s*$")

# フェンス閉じ行。
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")

# [置換後]/[現行]ラベル行の判定トークン。両者とも部分一致で検出する
# （`置換後:`・`[置換後]`・`現行:`・`[現行]`等の注記付き表現を対称に扱う）。
_REPLACEMENT_LABEL_TOKEN = "置換後"
_CURRENT_LABEL_TOKEN = "現行"
# 削除パターン（現行文言＋削除根拠の組）の削除根拠ブロックを判定するトークン。
# 削除根拠ブロックは対比適用対象外として無視し、直前の[現行]ブロックも未消費扱いを解除する。
_DELETION_RATIONALE_LABEL_TOKEN = "削除根拠"


def main() -> int:
    """検算のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="計画ファイルの[現行]/[置換後]対比ブロックを機械適用し、wc -l実測値と見込み行数の乖離を検出する。",
    )
    parser.add_argument(
        "plan_paths",
        nargs="+",
        type=pathlib.Path,
        help="検査対象の計画ファイル（複数指定可）",
    )
    args = parser.parse_args()

    total_violations = 0
    for plan_path in args.plan_paths:
        total_violations += _check_wc(plan_path)
    return 1 if total_violations > 0 else 0


def _check_wc(plan_path: pathlib.Path) -> int:
    """1計画ファイルを検算し、違反件数を返す。"""
    try:
        projected_map, blocks, orphan_paths = _parse_plan_file(plan_path)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"{plan_path}: 計画ファイルの読み込みに失敗: {exc}", file=sys.stderr)
        return 1

    grouped: dict[str, list[tuple[str, str]]] = {}
    order: list[str] = []
    for path, current, replacement in blocks:
        if path not in grouped:
            grouped[path] = []
            order.append(path)
        grouped[path].append((current, replacement))

    violations = 0
    for path in orphan_paths:
        print(
            f"{plan_path}: {path} の[現行]ブロックに対応する[置換後]ブロックが存在しない",
            file=sys.stderr,
        )
        violations += 1

    for path in order:
        violations += _check_one_file(plan_path, path, grouped[path], projected_map)
    return violations


def _check_one_file(
    plan_path: pathlib.Path,
    rel_path: str,
    diffs: list[tuple[str, str]],
    projected_map: dict[str, int],
) -> int:
    """1対象ファイルへ対比ブロック群を逐次適用し、乖離を検査する。違反件数を返す。

    正本は書き換えない（メモリ上で置換して行数のみ実測する）。
    """
    source = pathlib.Path(rel_path)
    if not source.exists():
        print(f"{plan_path}: 対象ファイル不在 {rel_path}", file=sys.stderr)
        return 1

    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"{plan_path}: 対象ファイル読込失敗 {rel_path} ({exc})", file=sys.stderr)
        return 1
    for current, replacement in diffs:
        if text.count(current) != 1:
            print(f"{plan_path}: {rel_path} [現行]文面が正本と一致しないか複数箇所へマッチする", file=sys.stderr)
            return 1
        text = text.replace(current, replacement)

    projected = projected_map.get(rel_path)
    if projected is None:
        print(f"{plan_path}: {rel_path} の見込み行数が対象ファイル一覧に未記載", file=sys.stderr)
        return 1

    # `wc -l`は改行文字の個数を数える。`splitlines()`は末尾改行の有無で結果がずれ得るため、
    # 改行文字数の直接カウントで`wc -l`実測値と一致させる。
    actual_lines = text.count("\n")
    drift = actual_lines - projected
    if abs(drift) > _ALLOWED_DRIFT:
        print(
            f"{plan_path}: {rel_path} 見込み{projected}行, 実測{actual_lines}行, 差{drift:+d}行",
            file=sys.stderr,
        )
        return 1
    return 0


def _parse_plan_file(
    plan_path: pathlib.Path,
) -> tuple[dict[str, int], list[tuple[str, str, str]], list[str]]:
    """計画ファイルを解析し、(見込み行数マップ, [(相対パス, 現行文言, 置換後文言), ...], 未消費[現行]パス一覧)を返す。

    見込み行数マップは`## 変更内容`配下`### 対象ファイル一覧`のチェックボックス項目から
    `- [ ] path（現行N行, 見込みM行）`形式を走査して収集する。
    既知パス集合は同チェックボックス項目のうち見込み行数の有無を問わず全パスを収集する。
    H3見出しに現れるパス候補が既知パス集合に含まれる場合のみ対比ブロック走査対象とする
    （`Makefile`・`Dockerfile`・`LICENSE`等の拡張子・区切りを持たないファイル名も検出できる）。
    対比ブロックは`## 変更内容`セクション配下に限定して走査する
    （`## 調査結果`等の他セクションに同形式の記述が偶発的に現れても対象外とするため）。
    """
    text = plan_path.read_text(encoding="utf-8")

    section = _extract_section(text, "## 変更内容")

    projected_map: dict[str, int] = {}
    known_paths_set: set[str] = set()
    if section is not None:
        for line in section.splitlines():
            m = _CHECKBOX_RE.match(line)
            if m:
                projected_map[m.group("path")] = int(m.group("projected"))
            m_path = _CHECKBOX_PATH_RE.match(line)
            if m_path:
                known_paths_set.add(m_path.group("path"))

    known_paths = frozenset(known_paths_set)
    if section is not None:
        blocks, orphan_paths = _extract_diff_blocks(section, known_paths)
    else:
        blocks, orphan_paths = [], []
    return projected_map, blocks, orphan_paths


def _extract_section(text: str, heading: str) -> str | None:
    """指定H2見出し直後から次のH2見出し直前までの本文を返す。見出しが無ければ`None`を返す。"""
    lines = text.splitlines()
    start: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        return None

    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].startswith("## ") and lines[idx].strip() != heading:
            end = idx
            break
    return "\n".join(lines[start:end])


def _extract_diff_blocks(section: str, known_paths: frozenset[str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """`## 変更内容`本文から(相対パス, 現行文言, 置換後文言)の一覧と、未消費[現行]パス一覧を返す。

    H3見出しでファイルパスの走査状態を更新しつつ、```textフェンスを順に走査する。
    フェンス直前の非空行のラベル（「置換後」「現行」「削除根拠」を含むか）で
    置換後・現行・削除の3種のブロックを判定する。
    現行ブロックは直近の未消費の現行ブロックとして保持し、続く置換後ブロックと対応付ける。
    削除根拠ブロックは対比対象外として無視し、直前の[現行]ブロックを削除確定として消費する
    （プラン規範上「削除は現行文言と削除根拠を対比形式で記載する」）。
    H3見出しのパス候補は、対象ファイル一覧から構築した既知パス集合に含まれるものだけを対象とする。
    ブロック走査終了時・H3切り替え時に[現行]ブロックが未消費のまま残っていた場合は違反として報告する。
    同一H3内で[現行]ブロックが[置換後]を挟まず連続した場合も、
    先行[現行]を未消費として違反へ記録する。
    """
    lines = section.splitlines()
    n = len(lines)
    blocks: list[tuple[str, str, str]] = []
    orphan_paths: list[str] = []

    current_path: str | None = None
    pending_current: str | None = None
    i = 0
    while i < n:
        line = lines[i]

        m_h3 = _H3_RE.match(line)
        if m_h3:
            if pending_current is not None and current_path is not None:
                orphan_paths.append(current_path)
            path = m_h3.group("path")
            # 対象ファイル一覧から構築した既知パス集合に含まれるH3のみ対象とする。
            # 「対象ファイル一覧」等の見出し語はファイルとして扱わない。
            current_path = path if path in known_paths else None
            pending_current = None
            i += 1
            continue

        if _TEXT_FENCE_OPEN_RE.match(line):
            label = _preceding_label(lines, i)
            i += 1
            content_lines: list[str] = []
            while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を読み飛ばす
            content = "\n".join(content_lines)

            if label == "current":
                if pending_current is not None and current_path is not None:
                    # 先行の[現行]ブロックが[置換後]と対応せずに次の[現行]で上書きされた
                    orphan_paths.append(current_path)
                pending_current = content
            elif label == "replacement" and pending_current is not None and current_path is not None:
                blocks.append((current_path, pending_current, content))
                pending_current = None
            elif label == "deletion":
                # 削除パターン（現行文言＋削除根拠の組）。削除根拠ブロック自体は対比対象外。
                # 直前の[現行]ブロックは削除確定として消費し、対比適用リストへは追加しない。
                pending_current = None
            continue

        i += 1

    if pending_current is not None and current_path is not None:
        orphan_paths.append(current_path)

    return blocks, orphan_paths


def _preceding_label(lines: list[str], fence_idx: int) -> str | None:
    """フェンス開始行`fence_idx`直前の非空行を調べ、「current」「replacement」「deletion」「None」を返す。"""
    j = fence_idx - 1
    while j >= 0 and lines[j].strip() == "":
        j -= 1
    if j < 0:
        return None
    stripped = lines[j].strip()
    # 削除根拠は最も具体的なトークンのため先に判定する。
    if _DELETION_RATIONALE_LABEL_TOKEN in stripped:
        return "deletion"
    # 置換後判定を先に行い、「現行との対比の置換後」等の両語併記行を置換後扱いに分類する。
    if _REPLACEMENT_LABEL_TOKEN in stripped:
        return "replacement"
    if _CURRENT_LABEL_TOKEN in stripped:
        return "current"
    return None


if __name__ == "__main__":
    sys.exit(main())
