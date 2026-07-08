#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル内の[現行]/[置換後]対比ブロックを機械適用し、wc -l実測値と見込み行数の乖離を検出する。

`agent-toolkit:plan-mode`工程7のメイン側セルフチェックから呼び出される。
アルゴリズムの詳細は`_parse_plan_file`・`_extract_diff_blocks`・`_check_one_file`各関数のdocstringを参照する。
出力形式は兄弟スクリプト`check_line_ref.py`と揃える（stderrへメッセージ、違反ありでexit 1）。

追記/縮減文面の算術照合は`_extract_addition_reduction_blocks`・`_collect_projection_bounds`・
`_check_addition_reduction_projection`各関数が担う。`## 変更内容`H3節配下の追記ブロック・縮減対象ブロックを
集計し、`### 対象ファイル一覧`チェックボックスの`（現行N行, 見込みM行）`表記との乖離を検出する。

対象ファイルが`.py`など実装コードの場合、`## 変更内容`節は追記文言案を逐語のフェンス内容として
記述せず、関数シグネチャ変更点の説明文で記述することが多い。
この場合、上記算術照合は説明文の行数を追記量と誤集計し偽陽性の乖離検出を報告し得る。
`.py`対象ファイルの見込み行数検証は本照合の出力に加え`wc -l`実測値との手動照合を併用する。
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
# 両形式から相対パス・現行行数（新設は0扱い）・見込み行数を抽出する。
_CHECKBOX_RE = re.compile(
    r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\n]+?)`?\s*"
    r"[（(](?:現行(?P<current>\d+)行|新設),\s*見込み(?P<projected>\d+)行[）)]"
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

# 縮減対象小見出し（`#### 縮減対象（xxx）`等）。H4見出しのみを対象とし統一する。
# 小見出し配下のtextブロックは縮減対象ブロックとして扱う。
_REDUCTION_HEADING_RE = re.compile(r"^####\s*縮減対象")

# 追記ブロックの連続検出トリガー文に含まれるトークン（部分一致）。
# 「追記文言案は次のとおり。」等の見出し文を検出したら、当該H3節境界まで出現する
# 連続するtextブロックを全て追記ブロックとして扱う。
_ADDITION_TRIGGER_TOKEN = "追記文言案"

# 追記/縮減対象ブロック内の1行目が「（挿入先・対象の説明）」のみで構成される場合、
# 計画執筆時の位置注記であり実際に対象ファイルへ挿入・保持される内容ではないため
# 行数集計から除外する（全角丸括弧で行全体を囲む場合のみ一致）。
_ANNOTATION_ONLY_RE = re.compile(r"^（.*）$")


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
    text = _read_text_or_none(plan_path)
    if text is None:
        print(f"{plan_path}: 計画ファイルの読み込みに失敗", file=sys.stderr)
        return 1

    projected_map, blocks, orphan_paths = _parse_plan_file(text)

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
    violations += _check_addition_reduction_projection(plan_path, text)
    return violations


def _read_text_or_none(path: pathlib.Path) -> str | None:
    """ファイルを読み込む。読み込み失敗時は`None`を返す。

    兄弟スクリプト`check_line_ref.py`の同名ヘルパーと同一パターン。
    `_check_wc`で1回だけ読み込み、`_parse_plan_file`・
    `_check_addition_reduction_projection`双方へ同一`text`を渡すことで
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
    text: str,
) -> tuple[dict[str, int], list[tuple[str, str, str]], list[str]]:
    """計画ファイル本文を解析し、(見込み行数マップ, [(相対パス, 現行文言, 置換後文言), ...], 未消費[現行]パス一覧)を返す。

    見込み行数マップは`## 変更内容`配下`### 対象ファイル一覧`のチェックボックス項目から
    `- [ ] path（現行N行, 見込みM行）`形式を走査して収集する。
    既知パス集合は同チェックボックス項目のうち見込み行数の有無を問わず全パスを収集する。
    H3見出しに現れるパス候補が既知パス集合に含まれる場合のみ対比ブロック走査対象とする
    （`Makefile`・`Dockerfile`・`LICENSE`等の拡張子・区切りを持たないファイル名も検出できる）。
    対比ブロックは`## 変更内容`セクション配下に限定して走査する
    （`## 調査結果`等の他セクションに同形式の記述が偶発的に現れても対象外とするため）。
    呼び出し元`_check_wc`が読み込み済みの`text`を受け取り、本関数ではファイルを再読み込みしない。
    """
    section = _extract_section(text, "## 変更内容")

    projected_map: dict[str, int] = {}
    if section is not None:
        for line in section.splitlines():
            m = _CHECKBOX_RE.match(line)
            if m:
                projected_map[m.group("path")] = int(m.group("projected"))

    known_paths = _collect_known_paths(section) if section is not None else frozenset()
    if section is not None:
        blocks, orphan_paths = _extract_diff_blocks(section, known_paths)
    else:
        blocks, orphan_paths = [], []
    return projected_map, blocks, orphan_paths


def _collect_known_paths(section: str) -> frozenset[str]:
    """`## 変更内容`本文の対象ファイル一覧チェックボックス項目から既知パス集合を構築する。

    見込み行数の記載有無を問わず全チェックボックス項目のパスを収集する。
    `_parse_plan_file`・`_extract_addition_reduction_blocks`の双方で共有するSSOT実装。
    """
    known_paths_set: set[str] = set()
    for line in section.splitlines():
        m_path = _CHECKBOX_PATH_RE.match(line)
        if m_path:
            known_paths_set.add(m_path.group("path"))
    return frozenset(known_paths_set)


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


# --- 追記/縮減文面の算術照合（`## 変更内容`集計値 と対象ファイル一覧`（現行N行, 見込みM行）`の乖離検出） ---


def _collect_projection_bounds(section: str) -> dict[str, tuple[int, int]]:
    """`### 対象ファイル一覧`チェックボックス項目から{パス: (現行行数, 見込み行数)}を抽出する。

    `新設`ファイルは現行行数0として扱う。`_CHECKBOX_RE`で抽出済みの見込み行数と同一の正規表現を用いる
    （SSOTは`対象ファイル一覧`チェックボックス項目の`（現行N行, 見込みM行）`表記のみとする）。
    """
    bounds: dict[str, tuple[int, int]] = {}
    for line in section.splitlines():
        m = _CHECKBOX_RE.match(line)
        if m:
            current = int(m.group("current")) if m.group("current") else 0
            bounds[m.group("path")] = (current, int(m.group("projected")))
    return bounds


def _extract_addition_reduction_blocks(section: str) -> dict[str, dict[str, int]]:
    """`## 変更内容`H3節配下の追記/縮減対象textブロックをファイルごとに集計する。

    `section`は`_extract_section(text, "## 変更内容")`で抽出済みのセクション文字列を受け取る
    （呼び出し側で抽出済みの`section`を再利用し、同一セクションの重複抽出を避ける）。
    追記ブロックは次の2条件いずれかで検出する。
    (1) H3節内で`_ADDITION_TRIGGER_TOKEN`（「追記文言案」）を含むトリガー文が出現した直後から、
        当該H3節境界（次のH3見出しまたはファイル末尾）までに現れる連続するtextブロック全体
    (2) フェンス直前非空行に「追記」または「追加」の語を含むtextブロック
        （部分一致。`"追記" in line or "追加" in line`相当。トリガー文が無い単発の追記ブロックを拾う）
    縮減対象ブロックは直前見出しが`#### 縮減対象`（H4見出しのみ、統一済み）配下のtextブロック。
    既存の`current`・`replacement`・`deletion`ラベル（`_preceding_label`が非Noneを返すブロック）に
    合致するブロックは対象外とし、二重集計を避ける。
    縮減対象見出し配下のブロックはトリガー文継続中でも縮減対象を優先する。
    戻り値はファイルパスをキーとし、addition（追記行数合計）とreduction（縮減対象行数合計）を
    値に持つ辞書。
    """
    result: dict[str, dict[str, int]] = {}
    known_paths = _collect_known_paths(section)
    lines = section.splitlines()
    n = len(lines)
    current_path: str | None = None
    in_reduction_heading = False
    in_addition_after_trigger = False
    i = 0
    while i < n:
        line = lines[i]

        m_h3 = _H3_RE.match(line)
        if m_h3:
            path = m_h3.group("path")
            current_path = path if path in known_paths else None
            in_reduction_heading = False
            in_addition_after_trigger = False
            i += 1
            continue

        if line.lstrip().startswith("#"):
            in_reduction_heading = bool(_REDUCTION_HEADING_RE.match(line.strip()))
            i += 1
            continue

        if _TEXT_FENCE_OPEN_RE.match(line):
            label = _preceding_label_for_addition_reduction(lines, i, in_reduction_heading, in_addition_after_trigger)
            i += 1
            content_lines: list[str] = []
            while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を読み飛ばす

            if label is not None and current_path is not None:
                entry = result.setdefault(current_path, {"addition": 0, "reduction": 0})
                counted_lines = content_lines
                if counted_lines and _ANNOTATION_ONLY_RE.match(counted_lines[0].strip()):
                    counted_lines = counted_lines[1:]
                entry[label] += len(counted_lines)
            continue

        if _ADDITION_TRIGGER_TOKEN in line:
            in_addition_after_trigger = True
        i += 1

    return result


def _preceding_label_for_addition_reduction(
    lines: list[str],
    fence_idx: int,
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
    既存の`current`・`replacement`・`deletion`ラベル（`_preceding_label`が非Noneを返す場合）は
    対象外として`None`を返す（従来通り無視）。
    """
    if _preceding_label(lines, fence_idx) is not None:
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


def _check_addition_reduction_projection(plan_path: pathlib.Path, text: str) -> int:
    """追記/縮減対象文面の集計値と対象ファイル一覧`（現行N行, 見込みM行）`宣言との乖離を検査し、違反件数を返す。

    照合式は「現行行数 + 追記行数 - 縮減対象行数 = 見込み行数」。
    追記/縮減対象ブロックが1件も存在しないファイルは検査対象外とする
    （対比ブロック方式のみを用いる従来計画ファイルへの影響を避けるため）。
    呼び出し元`_check_wc`が読み込み済みの`text`を受け取り、本関数ではファイルを再読み込みしない。
    """
    section = _extract_section(text, "## 変更内容")
    if section is None:
        return 0
    bounds = _collect_projection_bounds(section)
    actual = _extract_addition_reduction_blocks(section)

    violations = 0
    for path, counted in actual.items():
        if path not in bounds:
            continue
        current, projected = bounds[path]
        expected = current + counted["addition"] - counted["reduction"]
        drift = expected - projected
        if abs(drift) > _ALLOWED_DRIFT:
            print(
                f"{plan_path}: {path} 対象ファイル一覧宣言(現行{current}行,見込み{projected}行)と"
                f"追記/縮減対象集計からの見込み{expected}行(追記{counted['addition']}行,"
                f"縮減{counted['reduction']}行)の乖離が許容幅を超える(差{drift:+d}行)",
                file=sys.stderr,
            )
            violations += 1
    return violations


if __name__ == "__main__":
    sys.exit(main())
