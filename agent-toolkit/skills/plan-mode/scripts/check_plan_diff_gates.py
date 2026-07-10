#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル`## 変更内容`配下の差分ブロック本文へ事前機械検査を適用する。

抽出対象は次のラベル配下`text`フェンスブロック・トリガー文配下ブロック・
`（新設）`H3配下ブロック・`#### 縮減対象`配下ブロックとする。

- `[新設]`単独ラベル配下のtextフェンスブロック
- `[置換後]`単独ラベル配下のtextフェンスブロック
- `[置換後（全文）]`単独ラベル配下のtextフェンスブロック
- 追記/縮減トリガー文（「追記文言案」「追記内容:」「追記:」「追加:」「圧縮対象:」「圧縮後:」等）配下の直後textフェンスブロック
- `（新設）`H3配下のtextフェンスブロック
- `#### 縮減対象`小見出し配下のtextフェンスブロック

`[現行]`ラベル配下のブロックは削除予定の既存文言のため対象外とする。

兄弟スクリプト`check_wc_projection.py`は`[現行]`/`[置換後]`対比と`[削除根拠]`および
`「追記文言案」`トリガー・`#### 縮減対象`見出しを追記/縮減の行数集計対象とする。
本スクリプトのトリガー語彙は事前機械検査（縮退フレーズ・textlint・line-width）を
広く発火させる目的でwc集計側より広く定義しており、両者の対象範囲は意図的に非対称である。

各対象ブロック本文を次の検査へ通し、違反があれば`stderr`へ列挙してexit 1で終了する。
無違反時はexit 0で終了する。

- 縮退フレーズ検出: `agent-toolkit/scripts/_scope_escalation.py` CLI（stdin→exit 2で一致）
- textlint: `uvx pyfltr run-for-agent --commands=textlint --no-fix <tmpfile.md>`
    （一時ファイル拡張子を`.md`に固定して呼び出す）
- 127幅事前検査: `agent-toolkit/skills/writing-standards/scripts/check_line_width.py`
    （一時ファイル拡張子を`.md`に固定してsubprocess呼び出しし、違反行のstderr出力を回収する）

SSOTコメント: 共通トークンは兄弟モジュール`_plan_diff_parsing.py`へ集約済みでありimportで参照する。
意味論差異の温存方針は`_plan_diff_parsing.py`のdocstring参照。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator

# 共通モジュール読み込みのため本ファイルと同一ディレクトリを`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
from _plan_diff_parsing import (  # noqa: E402
    REDUCTION_HEADING_RE,
    TEXT_FENCE_OPEN_RE,
    extract_section_with_offset,
    is_matching_close,
)

# pylint: enable=wrong-import-position

# `agent-toolkit/scripts/_scope_escalation.py`の絶対パス。
# 本スクリプトは`agent-toolkit/skills/plan-mode/scripts/`配下のため、3階層遡って`scripts/`へ到達する。
_SCOPE_ESCALATION_CLI = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "_scope_escalation.py"

# `check_line_width.py` CLIの絶対パス。
# 本スクリプトから2階層遡って`writing-standards/scripts/check_line_width.py`へ到達する。
_CHECK_LINE_WIDTH_CLI = pathlib.Path(__file__).resolve().parents[2] / "writing-standards" / "scripts" / "check_line_width.py"

# `### <相対パス>`H3見出し。バッククォート付き・「（新設）」等の注記付きの双方に対応する。
_H3_RE = re.compile(r"^###\s+(?P<rest>.+)$")

# 対象ラベルの判定トークン（フェンス直後1行目のプレーンテキストラベルに部分一致する場合に該当扱いとする）。
_NEW_LABEL_TOKEN = "[新設]"
_REPLACEMENT_LABEL_TOKEN = "[置換後]"
_REPLACEMENT_FULL_LABEL_TOKEN = "[置換後（全文）]"
_CURRENT_LABEL_TOKEN = "[現行]"
_DELETION_RATIONALE_LABEL_TOKEN = "[削除根拠]"

# ラベル行判定用トークン一覧（fence直後1行目から本文抽出時に除外する対象）。
_ALL_LABEL_TOKENS = (
    _NEW_LABEL_TOKEN,
    _REPLACEMENT_LABEL_TOKEN,
    _REPLACEMENT_FULL_LABEL_TOKEN,
    _CURRENT_LABEL_TOKEN,
    _DELETION_RATIONALE_LABEL_TOKEN,
)

# 追記/縮減トリガー文に含まれるトークン（フェンス直前非空行に部分一致する場合、次のtextブロックを検査対象へ加える）。
_ADDITION_TRIGGER_TOKENS = ("追記文言案", "追記内容:", "追記:", "追加:", "圧縮対象:", "圧縮後:")

# `（新設）`注記付きH3見出しの判定トークン。
_NEW_H3_MARKER = "（新設）"


def main() -> int:
    """検査のエントリポイント。複数の計画ファイルを位置引数で受け取る。"""
    parser = argparse.ArgumentParser(
        description="計画ファイル`## 変更内容`配下の差分ブロック本文へ事前機械検査を適用する。",
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
        total_violations += len(_check_plan_file(plan_path))
    return 1 if total_violations > 0 else 0


def _check_plan_file(plan_path: pathlib.Path) -> list[str]:
    """1計画ファイルを走査し、違反メッセージ一覧を返す。副作用として`stderr`へも出力する。"""
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{plan_path}: 計画ファイルの読み込みに失敗 ({exc})"
        print(msg, file=sys.stderr)
        return [msg]

    violations: list[str] = []
    for h3_label, block_start_line, body in _extract_diff_blocks(text):
        category = _run_scope_escalation(body)
        if category is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` 縮退フレーズ検出（カテゴリ: {category}）"
            print(msg, file=sys.stderr)
            violations.append(msg)
        textlint_error = _run_textlint(body)
        if textlint_error is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` textlint違反\n{textlint_error}"
            print(msg, file=sys.stderr)
            violations.append(msg)
        line_width_error = _run_line_width(body)
        if line_width_error is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` line-width違反\n{line_width_error}"
            print(msg, file=sys.stderr)
            violations.append(msg)
    return violations


def _extract_diff_blocks(text: str) -> Iterator[tuple[str, int, str]]:
    """計画ファイル本文から検査対象ブロックを`(H3ラベル, ブロック開始行番号, ブロック本文)`で順に返す。

    `## 変更内容`セクションに限定して走査する。H3見出しの走査状態を更新しつつ`text`フェンスを検出する。
    各フェンスについて、フェンス直後1行目（fence内側）のラベル判定・トリガー継続中フラグ・
    見出しコンテキストで検査対象かを判断する。
    """
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return
    lines = section.splitlines()
    n = len(lines)
    current_h3: str = ""
    in_new_h3 = False
    in_reduction_heading = False
    trigger_active = False
    i = 0
    while i < n:
        line = lines[i]

        m_h3 = _H3_RE.match(line)
        if m_h3:
            rest = m_h3.group("rest").strip()
            current_h3 = rest
            in_new_h3 = _NEW_H3_MARKER in rest
            in_reduction_heading = False
            trigger_active = False
            i += 1
            continue

        if line.lstrip().startswith("####"):
            in_reduction_heading = bool(REDUCTION_HEADING_RE.match(line.strip()))
            i += 1
            continue

        m_open = TEXT_FENCE_OPEN_RE.match(line)
        if m_open:
            open_marker = m_open.group(1)
            block_start = i + 1  # フェンス本文の先頭行（1始まり）
            i += 1
            content_lines: list[str] = []
            while i < n and not is_matching_close(open_marker, lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を除外する
            label = _classify_block(content_lines, in_new_h3, in_reduction_heading, trigger_active)
            trigger_active = False
            if label is not None:
                # fence直後1行目のラベル行はtextlint検査対象から外す
                # （半角大かっこがtextlintのjtf-style/4.3.2で誤検出されるため）。
                body_lines = content_lines
                if body_lines and _is_label_line(body_lines[0]):
                    body_lines = body_lines[1:]
                body = "\n".join(body_lines)
                # 計画ファイル全体の行番号に換算する（section開始行 + section内オフセット）。
                absolute_line = section_start_line + block_start
                yield (current_h3, absolute_line, body)
            continue

        stripped = line.strip()
        if stripped and any(token in stripped for token in _ADDITION_TRIGGER_TOKENS):
            trigger_active = True
        i += 1


def _classify_block(
    content_lines: list[str],
    in_new_h3: bool,
    in_reduction_heading: bool,
    trigger_active: bool,
) -> str | None:
    """フェンス直後1行目のラベル・見出しコンテキスト・トリガー継続フラグから検査対象種別を判定する。

    優先順は次のとおり。
    1. `[現行]`・`[削除根拠]`ラベル配下は既存文言または削除説明のため検査対象外（`None`）
    2. `[新設]`・`[置換後]`・`[置換後（全文）]`ラベル配下は種別ラベルを返す
    3. `#### 縮減対象`見出し配下は`reduction`を返す
    4. `（新設）`H3配下は`new-h3`を返す
    5. 追記トリガー文出現後で当該H3節境界に未到達なら`addition`を返す
    それ以外は検査対象外として`None`を返す。
    """
    first = content_lines[0].strip() if content_lines else ""
    if first:
        if _CURRENT_LABEL_TOKEN in first:
            return None
        if _DELETION_RATIONALE_LABEL_TOKEN in first:
            return None
        if _REPLACEMENT_FULL_LABEL_TOKEN in first:
            return "replacement-full"
        if _REPLACEMENT_LABEL_TOKEN in first:
            return "replacement"
        if _NEW_LABEL_TOKEN in first:
            return "new"
    if in_reduction_heading:
        return "reduction"
    if in_new_h3:
        return "new-h3"
    if trigger_active:
        return "addition"
    return None


def _is_label_line(line: str) -> bool:
    """fence直後1行目が差分ラベル行に該当するかを判定する（本文抽出時の除外判定に用いる）。"""
    stripped = line.strip()
    return any(token in stripped for token in _ALL_LABEL_TOKENS)


def _run_scope_escalation(body: str) -> str | None:
    """`_scope_escalation.py` CLIをsubprocess呼び出しし、一致時カテゴリ識別子・未一致時Noneを返す。"""
    if not body:
        return None
    result = subprocess.run(
        [sys.executable, str(_SCOPE_ESCALATION_CLI)],
        input=body,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 2:
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else "unknown"
    return None


def _run_tmpfile_check(
    body: str,
    cmd_builder: Callable[[pathlib.Path], list[str]],
    label: str,
) -> str | None:
    """一時ファイル（`.md`拡張子）経由でCLI実行し、違反時に結合出力を返す共通ヘルパー。

    `cmd_builder`は一時ファイルパス（`pathlib.Path`）を受け取り、subprocess引数リストを返す関数。
    `label`は違反時のフォールバックメッセージ（`"{label} exit=N"`）に使う識別子。
    """
    if not body:
        return None
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        delete=False,
    ) as tmp:
        tmp.write(body)
        tmp_path = pathlib.Path(tmp.name)
    try:
        result = subprocess.run(
            cmd_builder(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            combined = (result.stdout or "") + (result.stderr or "")
            return combined.strip() or f"{label} exit={result.returncode}"
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def _run_textlint(body: str) -> str | None:
    """一時ファイル経由でtextlintを実行し、違反時stderr内容・未違反時Noneを返す。"""
    return _run_tmpfile_check(
        body,
        lambda p: ["uvx", "pyfltr", "run-for-agent", "--commands=textlint", "--no-fix", str(p)],
        "textlint",
    )


def _run_line_width(body: str) -> str | None:
    """一時ファイル経由で`check_line_width.py`を実行し、違反時stderr内容・未違反時Noneを返す。"""
    return _run_tmpfile_check(
        body,
        lambda p: [sys.executable, str(_CHECK_LINE_WIDTH_CLI), str(p)],
        "check_line_width",
    )


if __name__ == "__main__":
    sys.exit(main())
