#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル内の[現行]/[置換後]対比ブロックを機械適用し、対象ファイル実体との一意一致を検査する。

`agent-toolkit:plan-file-creator`の整合性チェック時のセルフチェックから呼び出される。
アルゴリズムの詳細は`_parse_plan_file`・`_extract_diff_blocks`・`_check_one_file`各関数のdocstringを参照する。
出力形式は兄弟スクリプト`check_line_ref.py`と揃える（stderrへメッセージ、違反ありでexit 1）。

追記/縮減文面の構造検出は`extract_addition_reduction_blocks`が担う。`## 変更内容`H3節配下の
追記ブロック・縮減対象ブロックの「存在有無」を、220行超過ファイルへの縮減対象H4欠落警告
（`_check_reduction_block_for_over_threshold_files`）・ラベルなし追記警告
（`_check_labelless_addition_for_over_threshold_files`）の判定材料として用いる。
行数の厳密な多寡は集計対象外とする（計画時・計画レビュー時の行数厳密計算を避ける方針のため）。

`[追記]`ラベルは`plan-file-diff-labels.md`「差分ラベル6種」節が定める正式ラベルであり、
`_leading_label`が`"addition"`を返す直接検出経路として扱う。ラベル行は集計対象から除外し、
兄弟スクリプト`check_plan_diff_gates.py`の`colloquial-check`併走と連携して、
計画段階でラベル直下のフェンス本文へ口語表現検査を到達させる。

共通要素は`_plan_diff_parsing.py`へ集約済みでありimportで参照する。
集約対象は`TEXT_FENCE_OPEN_RE`・`is_matching_close`・`REDUCTION_HEADING_RE`・
`iter_reduction_headings`・`extract_section_with_offset`・`FRONTMATTER_LABEL_RE`とする。
`FRONTMATTER_LABEL_RE`は`plan-file-diff-labels.md`「frontmatter変更用サブラベル」節が定める
`[追記（frontmatter）]`等4種の完全一致判定に用い、`_leading_label`・`_LABEL_ADDITION_ONLY_RE`双方から参照する。
`_H3_RE`のグループ名、`_CURRENT_LABEL_TOKEN`・`_REPLACEMENT_LABEL_TOKEN`の角括弧の有無は
本ファイル固有の意味論を持つため温存する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# 共通モジュール読み込みのため本ファイルと同一ディレクトリを`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from _plan_diff_parsing import (  # noqa: E402
    FRONTMATTER_LABEL_RE,
    REDUCTION_HEADING_RE,
    TEXT_FENCE_OPEN_RE,
    extract_section_with_offset,
    is_matching_close,
    iter_reduction_headings,
)

# pylint: enable=wrong-import-position


def _extract_section(text: str, heading: str) -> str | None:
    """`extract_section_with_offset`の本文のみを返す薄いラッパー。"""
    section, _ = extract_section_with_offset(text, heading)
    return section


# `## 変更内容`直下の対象ファイル一覧チェックボックス項目。
# 既存ファイル`- [ ] path（現行N行）`・合算表記`- [ ] path（現行N行、追記見込み+M行、合計K行）`と
# 新設ファイル`- [ ] path（新設）`の各形式から相対パス・現行行数（新設は0扱い）を抽出する。
# 合算表記の`追記見込み`・`合計`部分は現行行数抽出に用いないため任意グループとして扱い抽出対象から除外する。
_CHECKBOX_RE = re.compile(
    r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\n]+?)`?\s*[（(]"
    r"(?:現行(?P<current>\d+)行(?:、追記見込み\+\d+行、合計\d+行)?|新設)[）)]"
)

# 対象ファイル一覧の全チェックボックス項目（現行行数表記の有無を問わない）。
# 既知パス集合の構築に用いる。H3見出しのパス候補と突合してファイル判定する。
_CHECKBOX_PATH_RE = re.compile(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\s（(]+)`?")

# `### <相対パス>`H3見出し。バッククォート付き・「（新設）」等の注記付きの双方に対応する。
_H3_RE = re.compile(r"^###\s+`?(?P<path>[^`\s（(]+)`?")

# [置換後]/[現行]/[削除根拠]/[新設]/[置換後（全文）]ラベル行の判定トークン。
# fence直後1行目のプレーンテキストへ部分一致で検出する（fence内側形式）。
# 「置換後（全文）」は「置換後」の部分文字列を含むため、判定順で先に「置換後（全文）」を確認する。
_REPLACEMENT_LABEL_TOKEN = "置換後"
_REPLACEMENT_FULL_LABEL_TOKEN = "置換後（全文）"
_CURRENT_LABEL_TOKEN = "現行"
# 削除パターン（現行文言＋削除根拠の組）の削除根拠ブロックを判定するトークン。
# 削除根拠ブロックは対比適用対象外として無視し、直前の[現行]ブロックも未消費扱いを解除する。
_DELETION_RATIONALE_LABEL_TOKEN = "削除根拠"
_NEW_LABEL_TOKEN = "新設"
_ADDITION_LABEL_TOKEN = "追記"

# `FRONTMATTER_LABEL_RE`のキャプチャグループ1（角括弧・「（frontmatter）」を除いたトークン）から
# `_leading_label`の戻り値種別へ変換するマップ。`[新設（frontmatter）]`は
# `plan-file-diff-labels.md`「frontmatter変更用サブラベル」節が定める4種に含まないため対象外。
_FRONTMATTER_LABEL_TOKEN_TO_KIND: dict[str, str] = {
    _CURRENT_LABEL_TOKEN: "current",
    _REPLACEMENT_LABEL_TOKEN: "replacement",
    _DELETION_RATIONALE_LABEL_TOKEN: "deletion",
    _ADDITION_LABEL_TOKEN: "addition",
}

# 220行超過判定の閾値（`agent-toolkit:agent-standards`「文書サイズ上限」節が定める
# 220行以下収束を実装完了条件とし、超過ファイルには縮減計画の明示を要求する）。
_POST_IMPL_HARD_LIMIT_LINES = 220

# 追記ブロックの連続検出トリガー文に含まれるトークン（部分一致）。
# 「追記文言案は次のとおり。」等の見出し文を検出したら、当該H3節境界まで出現する
# 連続するtextブロックを全て追記ブロックとして扱う。
_ADDITION_TRIGGER_TOKEN = "追記文言案"

# 追記/縮減対象ブロック内の1行目が「（挿入先・対象の説明）」のみで構成される場合、
# 計画執筆時の位置注記であり実際に対象ファイルへ挿入・保持される内容ではないため
# 行数集計から除外する（全角丸括弧で行全体を囲む場合のみ一致）。
_ANNOTATION_ONLY_RE = re.compile(r"^（.*）$")

# 計画本文冒頭の許容リポジトリルート宣言（HTMLコメント形式`<!-- allowed-repo-root: /abs/path -->`）を抽出する。
# 宣言されたルート配下の絶対パスは対象ファイル一覧の絶対パス警告対象から除外する。
_ALLOWED_REPO_ROOT_RE = re.compile(r"<!--\s*allowed-repo-root:\s*(?P<root>[^\s]+?)\s*-->")

# 追記/縮減対象ブロック内の1行目が`[追記]`ラベル単独の場合、当該ラベル行は
# `plan-file-diff-labels.md`「差分ラベル6種」節が定める記法上の目印であり
# 対象ファイルへ挿入される内容ではないため行数集計から除外する。
# ラベル文字列は`_ADDITION_LABEL_TOKEN`をSSOTとし、正規表現側から参照する。
# `[追記]`・`[追記（frontmatter）]`・`[追記×N]`（NはASCII整数で1以上）の3パターンに対応する。
# `×N`修飾子は`plan-file-diff-labels.md`「差分ラベル6種」節が定める同一文面を複数箇所へ配置する場合の
# 書式であり、集計時に該当ブロックの行数をN倍して加算する。
# `[追記（frontmatter）]`サブラベルと`×N`修飾子の併用（`[追記×N（frontmatter）]`・
# `[追記（frontmatter）×N]`）は不受理とする（書式解釈の曖昧化を防ぐ）。
# 倍率は`[1-9][0-9]*`でASCII整数1以上に限定し、`[追記×0]`受理・Unicode数字受理を防ぐ。
_LABEL_ADDITION_ONLY_RE = re.compile(
    rf"^\[{re.escape(_ADDITION_LABEL_TOKEN)}(?:×(?P<multiplier>[1-9][0-9]*)|（frontmatter）)?\]$"
)


def main() -> int:
    """検査のエントリポイント。"""
    parser = argparse.ArgumentParser(
        description="計画ファイルの[現行]/[置換後]対比ブロックを機械適用し、対象ファイル実体との一意一致を検査する。",
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
    text = _read_text_or_none(plan_path)
    if text is None:
        print(f"{plan_path}: 計画ファイルの読み込みに失敗", file=sys.stderr)
        return 1

    # 220行超過ファイルの`#### 縮減対象（<ファイル名>）`H4見出し検査を実行する
    # （警告のみで違反件数には計上しない）。
    _check_reduction_block_for_over_threshold_files(plan_path, text)

    # 220行到達済みファイルへのラベルなしtextフェンス追記のみが計上される場合の警告を実行する
    # （警告のみで違反件数には計上しない）。
    _check_labelless_addition_for_over_threshold_files(plan_path, text)

    # 対象ファイル一覧の絶対パス表記を検出し、`<!-- allowed-repo-root: /abs/path -->`宣言済み
    # ルート配下のパスは除外したうえで残余のみ警告する（警告のみで違反件数には計上しない）。
    _check_absolute_paths_with_allowed_roots(plan_path, text)

    _current_map, blocks, orphan_paths = _parse_plan_file(text)

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
        violations += _check_one_file(plan_path, path, grouped[path])
    return violations


def _extract_allowed_repo_roots(text: str) -> list[str]:
    """計画本文中の`<!-- allowed-repo-root: /abs/path -->`宣言から許容ルート絶対パス一覧を抽出する。

    複数宣言時は宣言順に全て収集する。宣言が無い場合は空リストを返す。
    """
    return [m.group("root") for m in _ALLOWED_REPO_ROOT_RE.finditer(text)]


def _check_absolute_paths_with_allowed_roots(plan_path: pathlib.Path, text: str) -> int:
    """対象ファイル一覧の絶対パス表記のうち、許容ルート配下でないものを警告する。

    引数順・戻り値型規約は`(plan_path: pathlib.Path, text: str) -> int`とする。
    常に0を返し、違反件数の集計には影響させない設計とする（警告は情報提供扱い）。
    許容ルートの宣言記法は`<!-- allowed-repo-root: /abs/path -->`のHTMLコメント形式で、
    計画本文冒頭（または任意位置）に配置できる。
    """
    section = _extract_section(text, "## 変更内容")
    if section is None:
        return 0
    allowed_roots = _extract_allowed_repo_roots(text)
    absolute_paths: list[str] = []
    for line in section.splitlines():
        m = _CHECKBOX_PATH_RE.match(line)
        if not m:
            continue
        path = m.group("path")
        if not path.startswith("/"):
            continue
        if any(path == root or path.startswith(root.rstrip("/") + "/") for root in allowed_roots):
            continue
        absolute_paths.append(path)
    if absolute_paths:
        joined = ", ".join(f"`{p}`" for p in absolute_paths)
        print(
            f"{plan_path}: [warn] 対象ファイル一覧に許容ルート未宣言の絶対パスを検出: {joined}。"
            f"`<!-- allowed-repo-root: /abs/path -->`宣言で許容するか相対パスへ修正する",
            file=sys.stderr,
        )
    return 0


def _read_text_or_none(path: pathlib.Path) -> str | None:
    """ファイルを読み込む。読み込み失敗時は`None`を返す。

    兄弟スクリプト`check_line_ref.py`の同名ヘルパーと同一パターン。
    `_check_wc`で1回だけ読み込み、以降の各検査関数へ同一`text`を渡すことで
    同一計画ファイルの重複読み込みを避ける。
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _check_one_file(
    plan_path: pathlib.Path,
    rel_path: str,
    diffs: list[tuple[str, str]],
) -> int:
    """1対象ファイルへ対比ブロック群を逐次適用し、一意一致（転記の陳腐化防止）を検査する。違反件数を返す。

    正本は書き換えない（メモリ上で置換して行数のみ実測する）。
    実装完了後（`plan-impl-executor`工程3）の再実行にも対応するため、
    [置換後]文面が正本に単独で存在する場合は適用済み状態とみなして通過する
    （前後どちらの時点で実行しても同一結果を返す）。
    純追記パターン（[現行]文面が末尾に新規行を加えただけの[置換後]文面の真部分文字列となる場合）は、
    [置換後]適用後も[現行]文面がそのまま部分一致で残存するため、
    [現行]優先で判定すると二重適用（新規行の重複計上）が発生する。
    これを避けるため、[現行]が[置換後]の真部分文字列となる純追記パターンに限り、
    [置換後]文面の単独存在チェックを[現行]文面チェックより先に行う。
    純追記パターンでない通常の書き換えでは従来どおり[現行]チェックを優先し、
    [置換後]文面が対比ブロックと無関係な箇所に偶然一致するケースでの誤判定を避ける。
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
        is_pure_addition = replacement != "" and current in replacement
        # 実装完了後の再実行時、対象ファイルは既に[置換後]文面が反映済みで[現行]文面は消失している
        # （純追記パターンでは[現行]文面が部分文字列として残存する）。
        # 純追記パターンに限り、[置換後]文面が単独で存在する場合は適用済み状態とみなし、
        # textを変更せず次の対比ブロックへ進む。
        if is_pure_addition and text.count(replacement) == 1:
            continue
        occurrence = text.count(current)
        if occurrence == 1:
            text = text.replace(current, replacement)
            continue
        if occurrence == 0 and (replacement == "" or text.count(replacement) == 1):
            continue
        if occurrence == 0:
            print(
                f"{plan_path}: {rel_path} [現行]文面が正本内で0回検出（転記誤りの可能性）、[置換後]文面の反映も確認できない",
                file=sys.stderr,
            )
        else:
            print(
                f"{plan_path}: {rel_path} [現行]文面が正本内で{occurrence}回検出（一意化不足）、"
                f"[置換後]文面の反映も確認できない。[現行]ブロックへ周辺行を含めて一意化する必要",
                file=sys.stderr,
            )
        return 1
    return 0


def _parse_plan_file(
    text: str,
) -> tuple[dict[str, int], list[tuple[str, str, str]], list[str]]:
    """計画ファイル本文を解析し、(現行行数マップ, [(相対パス, 現行文言, 置換後文言), ...], 未消費[現行]パス一覧)を返す。

    現行行数マップは`## 変更内容`配下`### 対象ファイル一覧`のチェックボックス項目から
    `- [ ] path（現行N行）`形式を走査して収集する（新設ファイルは0扱い）。
    既知パス集合は同チェックボックス項目のうち現行行数表記の有無を問わず全パスを収集する。
    H3見出しに現れるパス候補が既知パス集合に含まれる場合のみ対比ブロック走査対象とする
    （`Makefile`・`Dockerfile`・`LICENSE`等の拡張子・区切りを持たないファイル名も検出できる）。
    対比ブロックは`## 変更内容`セクション配下に限定して走査する
    （`## 調査結果`等の他セクションに同形式の記述が偶発的に現れても対象外とするため）。
    呼び出し元`_check_wc`が読み込み済みの`text`を受け取り、本関数ではファイルを再読み込みしない。
    """
    section = _extract_section(text, "## 変更内容")

    current_map = _collect_current_line_counts(section) if section is not None else {}
    known_paths = _collect_known_paths(section) if section is not None else frozenset()
    if section is not None:
        blocks, orphan_paths = _extract_diff_blocks(section, known_paths)
    else:
        blocks, orphan_paths = [], []
    return current_map, blocks, orphan_paths


def _collect_known_paths(section: str) -> frozenset[str]:
    """`## 変更内容`本文の対象ファイル一覧チェックボックス項目から既知パス集合を構築する。

    現行行数表記の有無を問わず全チェックボックス項目のパスを収集する。
    `_parse_plan_file`・`extract_addition_reduction_blocks`の双方で共有するSSOT実装。
    """
    known_paths_set: set[str] = set()
    for line in section.splitlines():
        m_path = _CHECKBOX_PATH_RE.match(line)
        if m_path:
            known_paths_set.add(m_path.group("path"))
    return frozenset(known_paths_set)


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
    同一H3内で[現行]ブロックが[置換後]を介さず連続した場合も、
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

        m_open = TEXT_FENCE_OPEN_RE.match(line)
        if m_open:
            open_marker = m_open.group(1)
            i += 1
            content_lines: list[str] = []
            while i < n and not is_matching_close(open_marker, lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を除外する

            label = _leading_label(content_lines)
            # ラベル行はfence直後1行目に配置される。本文抽出時はラベル行を除外する。
            body_lines = content_lines[1:] if label is not None else content_lines
            content = "\n".join(body_lines)

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
            elif label == "replacement-full" and pending_current is not None:
                # [置換後（全文）]は全文置換のため文字列一致による部分置換適用（blocksへの追加）は行わない。
                # ただし直前の[現行]ブロックとの対応は成立しているため、孤児（orphan）扱いにはしない
                # （`extract_addition_reduction_blocks`の行数差算入経路と対応関係を一致させる）。
                pending_current = None
            # `new`は対比ペア対象外のためここでは記録しない（追記/縮減集計側では別途扱う）。
            continue

        i += 1

    if pending_current is not None and current_path is not None:
        orphan_paths.append(current_path)

    return blocks, orphan_paths


def _leading_label(content_lines: list[str]) -> str | None:
    """fence直後1行目のプレーンテキストラベルを調べ、種別を返す。

    返却値は`"current"`・`"replacement"`・`"replacement-full"`・`"deletion"`・`"new"`・`"addition"`のいずれか、
    ラベルが見つからない場合は`None`。

    ラベル種は`[現行]`・`[置換後]`・`[新設]`・`[置換後（全文）]`・`[削除根拠]`・`[追記]`の6種
    （`[追記]`は`[追記×N]`・`[追記（frontmatter）]`の派生形を含む）で、
    fence直後1行目の内容へ部分一致で検出する。
    「置換後（全文）」は「置換後」の部分文字列を含むため、判定順で先に「置換後（全文）」を確認する。

    frontmatter向けサブラベル（`[現行（frontmatter）]`等）は`FRONTMATTER_LABEL_RE`の完全一致で
    先に判定する。以降の部分一致判定は本体ラベル向けの既存ロジックを温存する。
    """
    if not content_lines:
        return None
    stripped = content_lines[0].strip()
    m_frontmatter = FRONTMATTER_LABEL_RE.match(stripped)
    if m_frontmatter:
        return _FRONTMATTER_LABEL_TOKEN_TO_KIND[m_frontmatter.group(1)]
    # 削除根拠は最も具体的なトークンのため先に判定する。
    if _DELETION_RATIONALE_LABEL_TOKEN in stripped:
        return "deletion"
    # 「置換後（全文）」は「置換後」を包含するため先に判定する。
    if _REPLACEMENT_FULL_LABEL_TOKEN in stripped:
        return "replacement-full"
    # 置換後判定を先に行い、「現行との対比の置換後」等の両語併記行を置換後扱いに分類する。
    if _REPLACEMENT_LABEL_TOKEN in stripped:
        return "replacement"
    if _CURRENT_LABEL_TOKEN in stripped:
        return "current"
    if _NEW_LABEL_TOKEN in stripped:
        return "new"
    if _ADDITION_LABEL_TOKEN in stripped:
        return "addition"
    return None


def _check_reduction_block_for_over_threshold_files(plan_path: pathlib.Path, text: str) -> int:
    """220行超過ファイル（現行220行超）対象時、対応する`#### 縮減対象（<ファイル名>）`H4見出しの存在を検証する。

    引数順・戻り値型規約は`(plan_path: pathlib.Path, text: str) -> int`とする。
    常に0を返し、違反件数の集計には影響させない設計とする（警告は情報提供扱いのため）。

    判定基準:
    - 対象ファイル一覧の現行行数が`_POST_IMPL_HARD_LIMIT_LINES`超のファイルを対象とする
    - `agent-toolkit:agent-standards`「文書サイズ上限」節に従い対象拡張子は`.md`・`.md.tmpl`に限定する
      （`.py`等のスクリプト・非Markdownファイルは判定対象から除外する）
    - 各対象ファイルに対応する`#### 縮減対象（<ファイル名>）`H4見出しが計画本文に存在するかを検証する
    - 対応する見出しが不在の場合、`f"{plan_path}: <警告内容>"`書式で警告を出力する
    """
    section = _extract_section(text, "## 変更内容")
    if section is None:
        return 0

    current_map = _collect_current_line_counts(section)
    over_threshold_files = [
        path
        for path, current in current_map.items()
        if current > _POST_IMPL_HARD_LIMIT_LINES and (path.endswith(".md") or path.endswith(".md.tmpl"))
    ]
    if not over_threshold_files:
        return 0

    # `#### 縮減対象（<ファイル名>）`H4見出しからファイル名を`iter_reduction_headings`で収集する
    # （SSOTは`_plan_diff_parsing.iter_reduction_headings`）。
    heading_files: set[str] = set(iter_reduction_headings(section))
    addition_reduction = extract_addition_reduction_blocks(section)

    for path in over_threshold_files:
        entry = addition_reduction.get(path, {})
        if entry.get("replacement_pair_count", 0) > 0 or entry.get("reduction", 0) > 0:
            continue
        # 対象ファイルパスの末尾名（basename）と一致・完全パス一致・
        # basename含有修飾名（例:「agent-standards SKILL.md」）のいずれかで照合する。
        # 計画本文の縮減対象H4はファイル名のみ・修飾名のいずれの表記も許容する。
        basename = path.rsplit("/", 1)[-1]
        basename_pattern = re.compile(rf"\b{re.escape(basename)}\b")
        if (
            path in heading_files
            or basename in heading_files
            or any(basename_pattern.search(heading) for heading in heading_files)
        ):
            continue
        print(
            f"[warn] {plan_path}: 220行超過ファイル{path}"
            f"（現行{current_map[path]}行）に対応する"
            f"`#### 縮減対象（{basename}）`H4見出しが不在",
            file=sys.stderr,
        )
    return 0


def _collect_current_line_counts(section: str) -> dict[str, int]:
    """`### 対象ファイル一覧`チェックボックス項目から{パス: 現行行数}を抽出する。

    `新設`ファイルは現行行数0として扱う（SSOTは`対象ファイル一覧`チェックボックス項目の
    `（現行N行）`表記のみとする）。
    """
    current_map: dict[str, int] = {}
    for line in section.splitlines():
        m = _CHECKBOX_RE.match(line)
        if m:
            current_map[m.group("path")] = int(m.group("current")) if m.group("current") else 0
    return current_map


def _check_labelless_addition_for_over_threshold_files(plan_path: pathlib.Path, text: str) -> int:
    """220行到達済みファイル（現行220行超）対象時、ラベルなしtextフェンスによる追記のみが計上される場合に警告する。

    引数順・戻り値型規約は`(plan_path: pathlib.Path, text: str) -> int`とする。
    常に0を返し、違反件数の集計には影響させない設計とする（警告は情報提供扱い）。

    判定基準:
    - 対象ファイル一覧の現行行数が`_POST_IMPL_HARD_LIMIT_LINES`超のファイルを対象とする
    - `agent-toolkit:agent-standards`「文書サイズ上限」節に従い対象拡張子は`.md`・`.md.tmpl`に限定する
    - 集計結果でラベルなし追記行数（`addition_labelless`）が0超かつ縮減対象行数が0の対象ファイルを警告対象とする
      （ラベル付き`[現行]`/`[置換後]`ペアまたは`[追記]`ラベルで書けば`_check_one_file`の対比検証経路
      または`[追記]`直接検出経路へ載り、ラベルなし追記のみを警告対象として抽出できるため）
    """
    section = _extract_section(text, "## 変更内容")
    if section is None:
        return 0

    current_map = _collect_current_line_counts(section)
    over_threshold_files = {
        path
        for path, current in current_map.items()
        if current > _POST_IMPL_HARD_LIMIT_LINES and (path.endswith(".md") or path.endswith(".md.tmpl"))
    }
    if not over_threshold_files:
        return 0

    addition_reduction = extract_addition_reduction_blocks(section)
    for path in over_threshold_files:
        stats = addition_reduction.get(path, {})
        labelless = stats.get("addition_labelless", 0)
        reduced = stats.get("reduction", 0)
        if labelless > 0 and reduced == 0:
            print(
                f"[warn] {plan_path}: 220行到達済みファイル{path}"
                f"（現行{current_map[path]}行）への追記に"
                f"`[現行]`/`[置換後]`または`[追記]`ラベルが付いていない。差分ラベル付与を検討",
                file=sys.stderr,
            )
    return 0


def _new_addition_reduction_entry() -> dict[str, int]:
    """`extract_addition_reduction_blocks`の集計エントリ初期値をSSOTとして返す。"""
    return {
        "replacement_pair_count": 0,
        "reduction": 0,
        "addition_labelless": 0,
    }


def extract_addition_reduction_blocks(section: str) -> dict[str, dict[str, int]]:
    """`## 変更内容`H3節配下の追記/縮減対象textブロックの「存在有無」をファイルごとに集計する。

    行数の厳密な多寡ではなく、縮減対象H4欠落警告・ラベルなし追記警告の判定材料に限定した
    構造的な検出を行う（計画時・計画レビュー時の行数厳密計算を避ける方針のため）。
    `section`は`_extract_section(text, "## 変更内容")`で抽出済みのセクション文字列を受け取る
    （呼び出し側で抽出済みの`section`を再利用し、同一セクションの重複抽出を避ける）。
    追記ブロックは次の2条件いずれかで検出する。
    (1) H3節内で`_ADDITION_TRIGGER_TOKEN`（「追記文言案」）を含むトリガー文が出現した直後から、
        当該H3節境界（次のH3見出しまたはファイル末尾）までに現れる連続するtextブロック全体
    (2) フェンス直前非空行に「追記」または「追加」の語を含むtextブロック
        （部分一致。`"追記" in line or "追加" in line`相当。トリガー文が無い単発の追記ブロックを拾う）
    縮減対象ブロックは直前見出しが`#### 縮減対象`（H4見出しのみ、統一済み）配下のtextブロック。
    既存の`new`ラベル（`_leading_label`が非Noneを返すブロック）は対象外とする。
    縮減対象見出し配下のブロックはトリガー文継続中でも縮減対象を優先する。
    `## 変更内容`H3節配下で[現行]→[削除根拠]のペア出現を検出した場合も、[現行]→[置換後]ペアと同型の
    diff計算経路（置換後文言を空リストとして扱う）で処理し、直前の[現行]ブロックの行数
    （先頭ラベル行および位置注記1行の除外後）を縮減対象行数へ加算する
    （削除パターンの[削除根拠]ブロック自体は`_leading_label`経由で対比対象外のため、
    `_preceding_label_for_addition_reduction`側の縮減判定とは別経路で処理し二重集計を避ける）。
    戻り値はファイルパスをキーとし、replacement_pair_count（`[現行]/[置換後]`ペアの存在カウント。
    縮減対象H4欠落警告の抑止条件に用いる）・reduction（縮減対象行数合計）・
    addition_labelless（トリガー継続・追記見出し由来のラベルなし追記行数。ラベルなし追記警告の
    判定材料に用いる）を値に持つ辞書。
    同一ファイル内で`[追記]`ラベル付き追記とラベルなし追記が混在する場合、`reduction`が0のまま
    `addition_labelless`が0超であればラベルなし追記が未解消として残っているとみなし警告対象とする。

    frontmatterサブラベル（`[追記（frontmatter）]`・`[現行（frontmatter）]`・
    `[置換後（frontmatter）]`・`[削除根拠（frontmatter）]`）は`_leading_label`が本体ラベルと
    同じ種別（"addition"・"current"・"replacement"・"deletion"）を返すため、
    本関数内では本体ラベルとラベル種別ごとに同一の集計経路へ合算する。
    ラベル行自体の除外判定（`_LABEL_ADDITION_ONLY_RE`・`_leading_label(pending_lines) is not None`）は
    frontmatterサブラベルにも対応済みのため、本体/frontmatterの区別で集計ロジックを分岐させない。

    `[現行]`→`[置換後]`ペアの行数差のうち負値（縮減）を`reduction`へ加算する。
    同ペアの存在自体（行数差の正負を問わない）を`replacement_pair_count`へ記録し、
    縮減対象H4欠落警告の抑止条件に用いる。
    """
    result: dict[str, dict[str, int]] = {}
    known_paths = _collect_known_paths(section)
    lines = section.splitlines()
    n = len(lines)
    current_path: str | None = None
    in_reduction_heading = False
    in_addition_after_trigger = False
    pending_current: list[str] | None = None
    i = 0
    while i < n:
        line = lines[i]

        m_h3 = _H3_RE.match(line)
        if m_h3:
            path = m_h3.group("path")
            current_path = path if path in known_paths else None
            in_reduction_heading = False
            in_addition_after_trigger = False
            pending_current = None
            i += 1
            continue

        if line.lstrip().startswith("#"):
            in_reduction_heading = bool(REDUCTION_HEADING_RE.match(line.strip()))
            i += 1
            continue

        m_open = TEXT_FENCE_OPEN_RE.match(line)
        if m_open:
            open_marker = m_open.group(1)
            fence_start_idx = i
            i += 1
            content_lines: list[str] = []
            while i < n and not is_matching_close(open_marker, lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を除外する

            # [現行]→[削除根拠]ペア検出経路（既存の`_preceding_label_for_addition_reduction`とは
            # 独立して実行し、削除パターンの[現行]行数を縮減対象へ加算する）。
            leading = _leading_label(content_lines)
            if leading == "current":
                pending_current = content_lines
            elif (
                leading in {"replacement", "replacement-full", "deletion"}
                and pending_current is not None
                and current_path is not None
            ):
                # 削除ペア（[現行]＋[削除根拠]）も置換ペア（[現行]＋[置換後]）と同型の
                # diff計算経路で処理する。削除ペアは置換後文言を空リストとして扱う。
                current_lines_effective: list[str] = list(pending_current)
                if current_lines_effective and _leading_label(current_lines_effective) is not None:
                    current_lines_effective = current_lines_effective[1:]
                if current_lines_effective and _ANNOTATION_ONLY_RE.match(current_lines_effective[0].strip()):
                    current_lines_effective = current_lines_effective[1:]
                replacement_lines_effective: list[str] = [] if leading == "deletion" else list(content_lines)
                if replacement_lines_effective and _leading_label(replacement_lines_effective) is not None:
                    replacement_lines_effective = replacement_lines_effective[1:]
                diff = len(replacement_lines_effective) - len(current_lines_effective)
                entry = result.setdefault(current_path, _new_addition_reduction_entry())
                if diff < 0:
                    entry["reduction"] += -diff
                entry["replacement_pair_count"] += 1
                pending_current = None
            elif leading is not None:
                pending_current = None

            label = _preceding_label_for_addition_reduction(
                lines,
                fence_start_idx,
                content_lines,
                in_reduction_heading,
                in_addition_after_trigger,
            )

            if label is not None and current_path is not None:
                entry = result.setdefault(current_path, _new_addition_reduction_entry())
                counted_lines = content_lines
                multiplier = 1
                label_match = _LABEL_ADDITION_ONLY_RE.match(counted_lines[0].strip()) if counted_lines else None
                is_label_line_first = label_match is not None
                if label_match is not None:
                    multiplier_group = label_match.group("multiplier")
                    if multiplier_group is not None:
                        multiplier = int(multiplier_group)
                    counted_lines = counted_lines[1:]
                if counted_lines and _ANNOTATION_ONLY_RE.match(counted_lines[0].strip()):
                    counted_lines = counted_lines[1:]
                added = len(counted_lines) * multiplier
                if label == "addition":
                    if not is_label_line_first:
                        entry["addition_labelless"] += added
                else:
                    entry[label] += added
            continue

        if _ADDITION_TRIGGER_TOKEN in line:
            in_addition_after_trigger = True
        i += 1

    return result


def _preceding_label_for_addition_reduction(
    lines: list[str],
    fence_idx: int,
    content_lines: list[str],
    in_reduction_heading: bool,
    in_addition_after_trigger: bool,
) -> str | None:
    """縮減対象見出し（H4）コンテキスト・追記トリガー継続中フラグ・フェンス直前非空行から判定する。

    縮減対象ブロックの判定は`#### 縮減対象`H4見出し配下であることのみを条件とする（見出し条件へ統一）。
    追記ブロックの判定は次の優先順で行う。
    (1) 縮減対象見出し配下でなく、かつ「追記文言案」トリガー文出現後で当該H3節境界に未到達の場合は
        無条件で追記と判定する（トリガー継続中フラグ`in_addition_after_trigger`）
    (2) トリガー継続中でない場合はフェンス直前非空行に「追記」または「追加」の語を含むかで判定する
        （部分一致。`"追記" in line or "追加" in line`相当）
    `[追記]`ラベルによる直接検出（`_leading_label(content_lines) == "addition"`）を
    追記判定の第1候補として採用する。既存の`current`・`replacement`・`replacement-full`・
    `deletion`・`new`ラベル（`_leading_label`が"addition"以外の非Noneを返す場合）は
    対象外として`None`を返す（従来通り無視）。
    ラベル種は`[現行]`・`[置換後]`・`[新設]`・`[置換後（全文）]`・`[削除根拠]`・`[追記]`の6種
    （`[追記]`は`[追記×N]`・`[追記（frontmatter）]`の派生形を含む）で、
    fence直後1行目のプレーンテキストへ部分一致で検出する。
    """
    leading = _leading_label(content_lines)
    if leading == "addition":
        return "addition"
    if leading is not None:
        return None

    if in_reduction_heading:
        return "reduction"

    if in_addition_after_trigger:
        return "addition"

    j = fence_idx - 1
    while j >= 0 and lines[j].strip() == "":
        j -= 1
    if j >= 0:
        stripped = lines[j].strip()
        if "追記" in stripped or "追加" in stripped:
            return "addition"
    return None


if __name__ == "__main__":
    sys.exit(main())
