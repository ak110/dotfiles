#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル`## 変更内容`配下の差分ブロック本文へ事前機械検査を適用する。

抽出対象は次の3種のラベル配下`text`フェンスブロックと、
兄弟スクリプト`check_wc_projection.py`の追記/縮減検出経路と同等のカバレッジを持つ
トリガー文配下ブロック・`（新設）`H3配下ブロック・`#### 縮減対象`配下ブロックとする。

- `[新設]`単独ラベル配下のtextフェンスブロック
- `[置換後]`単独ラベル配下のtextフェンスブロック
- `[置換後（全文）]`単独ラベル配下のtextフェンスブロック
- 追記/縮減トリガー文（「追記文言案」「追記内容:」「追記:」「追加:」「圧縮対象:」等）配下の直後textフェンスブロック
- `（新設）`H3配下のtextフェンスブロック
- `#### 縮減対象`小見出し配下のtextフェンスブロック

`[現行]`ラベル配下のブロックは削除予定の既存文言のため対象外とする。

各対象ブロック本文を次の検査へ通し、違反があれば`stderr`へ列挙してexit 1で終了する。
無違反時はexit 0で終了する。

- 縮退フレーズ検出: `agent-toolkit/scripts/_scope_escalation.py` CLI（stdin→exit 2で一致）
- textlint: `uvx pyfltr run-for-agent --commands=textlint --no-fix <tmpfile.md>`
    （一時ファイル拡張子を`.md`に固定して呼び出す）

SSOTコメント: ブロック抽出ロジックの主要トークンは兄弟スクリプト`check_wc_projection.py`と
役割が重なる（`_TEXT_FENCE_OPEN_RE`・`_FENCE_CLOSE_RE`・`_H3_RE`など）。
共通ヘルパーの切り出しは本スクリプト単体では実施せず、
将来2箇所以上の乖離リスクが顕在化した時点で`_plan_diff_parsing.py`等への集約を検討する。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator

# `agent-toolkit/scripts/_scope_escalation.py`の絶対パス。
# 本スクリプトは`agent-toolkit/skills/plan-mode/scripts/`配下のため、3階層遡って`scripts/`へ到達する。
_SCOPE_ESCALATION_CLI = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "_scope_escalation.py"

# フェンス開閉判定。`text`フェンスのみを対象とする（`python`・`bash`等の骨格提示ブロックは検査対象外）。
_TEXT_FENCE_OPEN_RE = re.compile(r"^```text\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")

# 汎用フェンス開始・終了判定（```pythonや~~~等、言語指定・記号種別を問わない）。
# `_extract_section`のH2見出し境界判定でフェンス内の`## `様の行を除外するために用いる。
_FENCE_RE = re.compile(r"^( *)(```+|~~~+)")

# `### <相対パス>`H3見出し。バッククォート付き・「（新設）」等の注記付きの双方に対応する。
_H3_RE = re.compile(r"^###\s+(?P<rest>.+)$")

# 対象ラベルの判定トークン（フェンス直前非空行に部分一致する場合に該当ラベル扱いとする）。
_NEW_LABEL_TOKEN = "[新設]"
_REPLACEMENT_LABEL_TOKEN = "[置換後]"
_REPLACEMENT_FULL_LABEL_TOKEN = "[置換後（全文）]"
_CURRENT_LABEL_TOKEN = "[現行]"

# 追記/縮減トリガー文に含まれるトークン（フェンス直前非空行に部分一致する場合、次のtextブロックを検査対象へ加える）。
_ADDITION_TRIGGER_TOKENS = ("追記文言案", "追記内容:", "追記:", "追加:", "圧縮対象:")

# 縮減対象小見出し（`#### 縮減対象（xxx）`等）。H4見出しのみを対象とする。
_REDUCTION_HEADING_RE = re.compile(r"^####\s*縮減対象")

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
    return violations


def _extract_diff_blocks(text: str) -> Iterator[tuple[str, int, str]]:
    """計画ファイル本文から検査対象ブロックを`(H3ラベル, ブロック開始行番号, ブロック本文)`で順に返す。

    `## 変更内容`セクションに限定して走査する。H3見出しの走査状態を更新しつつ`text`フェンスを検出する。
    各フェンスについて、直前非空行のラベル判定・トリガー継続中フラグ・見出しコンテキストで検査対象かを判断する。
    """
    section, section_start_line = _extract_section_with_offset(text, "## 変更内容")
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
            in_reduction_heading = bool(_REDUCTION_HEADING_RE.match(line.strip()))
            i += 1
            continue

        if _TEXT_FENCE_OPEN_RE.match(line):
            label = _classify_block(lines, i, in_new_h3, in_reduction_heading, trigger_active)
            block_start = i + 1  # フェンス本文の先頭行（1始まり）
            i += 1
            content_lines: list[str] = []
            while i < n and not _FENCE_CLOSE_RE.match(lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # 閉じフェンス行を除外する
            trigger_active = False
            if label is not None:
                body = "\n".join(content_lines)
                # 計画ファイル全体の行番号に換算する（section開始行 + section内オフセット）。
                absolute_line = section_start_line + block_start
                yield (current_h3, absolute_line, body)
            continue

        stripped = line.strip()
        if stripped and any(token in stripped for token in _ADDITION_TRIGGER_TOKENS):
            trigger_active = True
        i += 1


def _classify_block(
    lines: list[str],
    fence_idx: int,
    in_new_h3: bool,
    in_reduction_heading: bool,
    trigger_active: bool,
) -> str | None:
    """フェンス直前ラベル・見出しコンテキスト・トリガー継続フラグから検査対象種別を判定する。

    優先順は次のとおり。
    1. `[現行]`ラベル配下は削除予定の既存文言のため検査対象外とする（`None`を返す）
    2. `[新設]`・`[置換後]`・`[置換後（全文）]`ラベル配下は種別ラベルを返す
    3. `#### 縮減対象`見出し配下は`reduction`を返す
    4. `（新設）`H3配下は`new-h3`を返す
    5. 追記トリガー文出現後で当該H3節境界に未到達なら`addition`を返す
    それ以外は検査対象外として`None`を返す。
    """
    label_line = _preceding_non_empty(lines, fence_idx)
    if label_line is not None:
        if _CURRENT_LABEL_TOKEN in label_line:
            return None
        if _REPLACEMENT_FULL_LABEL_TOKEN in label_line:
            return "replacement-full"
        if _REPLACEMENT_LABEL_TOKEN in label_line:
            return "replacement"
        if _NEW_LABEL_TOKEN in label_line:
            return "new"
    if in_reduction_heading:
        return "reduction"
    if in_new_h3:
        return "new-h3"
    if trigger_active:
        return "addition"
    return None


def _preceding_non_empty(lines: list[str], fence_idx: int) -> str | None:
    """フェンス開始行`fence_idx`直前の非空行を返す（前後空白は除去）。存在しない場合は`None`。"""
    j = fence_idx - 1
    while j >= 0 and lines[j].strip() == "":
        j -= 1
    if j < 0:
        return None
    return lines[j].strip()


def _iter_non_fenced_lines(lines: list[str], start: int = 0) -> Iterator[tuple[int, str]]:
    """```・~~~フェンス内の行を除外し、(行番号, 行内容)を順に返す。"""
    in_fence = False
    fence_marker = ""
    for idx in range(start, len(lines)):
        line = lines[idx]
        m_fence = _FENCE_RE.match(line)
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


def _extract_section_with_offset(text: str, heading: str) -> tuple[str | None, int]:
    """指定H2見出し直後から次のH2見出し直前までの本文と、本文の開始行番号（1始まり）を返す。"""
    lines = text.splitlines()
    start: int | None = None
    for idx, line in _iter_non_fenced_lines(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        return None, 0

    end = len(lines)
    for idx, line in _iter_non_fenced_lines(lines, start):
        if line.startswith("## ") and line.strip() != heading:
            end = idx
            break
    return "\n".join(lines[start:end]), start


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


def _run_textlint(body: str) -> str | None:
    """一時ファイル（`.md`拡張子）経由でtextlint実行、違反時stderr内容・未違反時Noneを返す。"""
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
            [
                "uvx",
                "pyfltr",
                "run-for-agent",
                "--commands=textlint",
                "--no-fix",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            combined = (result.stdout or "") + (result.stderr or "")
            return combined.strip() or f"textlint exit={result.returncode}"
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
