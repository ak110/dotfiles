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

- `## 変更内容`本文のfence外側配置ラベルおよび全角化ラベルの全文一括検査（exit 1違反）
- 縮退フレーズ検出: `agent-toolkit/scripts/_scope_escalation.py` CLI（stdin→exit 2で一致）
- textlint: `uvx pyfltr run-for-agent --commands=textlint --no-fix <tmpfile.md>`
    （一時ファイル拡張子を`.md`に固定して呼び出す）
- 127幅検査: `agent-toolkit/skills/writing-standards/scripts/check_line_width.py`
    （一時ファイル拡張子を`.md`に固定してsubprocess呼び出しし、違反行のstderr出力を回収する）
- 対象ファイル一覧の`agent-toolkit/`配下パスに対するversion bumpステップ欠落の全文一括検査（warn出力のみ）
- `## 実行方法`にbump stepが記載されている場合のmanifest対象ファイル記載欠落の全文一括検査（warn出力のみ）
- 対象ファイル一覧に規範ファイル（`agent-toolkit/rules/*.md`等）を含み、`## 変更内容`本文の
    差分ブロックで新規H2以深節見出しの追加を検出したが`## 調査結果`配下に`### 遡及スキャン結果`
    小見出しが存在しない場合の全文一括検査（warn出力のみ）

SSOTコメント: 共通トークンは兄弟モジュール`_plan_diff_parsing.py`へ集約済みでありimportで参照する。
意味論差異の温存方針は`_plan_diff_parsing.py`のdocstring参照。

統合ランナー`check_plan_file.py`向けに、差分ブロック抽出とtextlint/line-width起動の責務を分離した
`_extract_diff_blocks(plan_path)`・`_check_extracted_paths(paths)`も公開する。抽出済み一時ファイル群を
まとめてtextlint 1回・`check_line_width.py` 1回のsubprocess呼び出しへ渡し、多重起動を避ける。
単独CLI実行（`main()`経由の`_check_plan_file`）はブロック単位のsubprocess起動のまま変更しない。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator

# 共通モジュール読み込みのため本ファイルと同一ディレクトリおよび`agent-toolkit/scripts/`を`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
# pylint: disable=wrong-import-position
from _plan_diff_parsing import (  # noqa: E402
    REDUCTION_HEADING_RE,
    TEXT_FENCE_OPEN_RE,
    extract_section_with_offset,
    is_matching_close,
)
from _plan_format import (  # noqa: E402
    extract_target_files_from_changes,
    has_bump_step_when_required,
    has_manifest_files_when_bump_step_present,
    is_agent_doc_target_file,
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

# H3見出し内のバッククォート付きファイル名抽出用。
_H3_FILE_RE = re.compile(r"`([^`]+)`")

# 散文系lint（textlint）を適用する対象拡張子。
_PROSE_EXTENSIONS = (".md", ".md.tmpl")

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

# fence外側配置検出用: ラベル文言単独行（前後空白のみ許容）。
_OUTER_LABEL_LINE_RE = re.compile(r"^\s*(?:\[現行\]|\[置換後\])\s*$")

# 全角化ラベル検出用: textlint autofixで閉じ括弧が全角化された`[現行］`／`[置換後］`。
_FULLWIDTH_LABEL_RE = re.compile(r"(?:\[現行］|\[置換後］)")

# fb-3: 新規H2以深節見出し検出用。`pretooluse.py:2297`付近の
# `_RETROACTIVE_SCAN_NEW_HEADING_PATTERN`と同じ正規表現を採用する。
_NEW_NORM_HEADING_RE = re.compile(r"^##[#]* .+$", re.MULTILINE)

# fb-3: `### 遡及スキャン結果`小見出し検出用。
_RETROACTIVE_SCAN_HEADING_RE = re.compile(r"^###\s+遡及スキャン結果\s*$", re.MULTILINE)


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
    violations.extend(_check_outer_label_placement(plan_path, text))
    for h3_label, block_start_line, body, h3_ext in _iter_diff_blocks(text):
        category = _run_scope_escalation(body)
        if category is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` 縮退フレーズ検出（カテゴリ: {category}）"
            print(msg, file=sys.stderr)
            violations.append(msg)
        if h3_ext in _PROSE_EXTENSIONS:
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
    bump_warning = _check_bump_step(plan_path, text)
    if bump_warning is not None:
        print(bump_warning, file=sys.stderr)
    manifest_warning = _check_manifest_files_when_bump_step(plan_path, text)
    if manifest_warning is not None:
        print(manifest_warning, file=sys.stderr)
    retroactive_scan_warning = _check_retroactive_scan_when_new_norm_section(plan_path, text)
    if retroactive_scan_warning is not None:
        print(retroactive_scan_warning, file=sys.stderr)
    return violations


def _check_bump_step(plan_path: pathlib.Path, text: str) -> str | None:
    """計画ファイル本文へversion bumpステップ要件を照合する。違反時は警告メッセージを返す。

    判定ロジックのSSOTは`_plan_format.has_bump_step_when_required`。
    pretooluse.py側の`_check_plan_file_bump_step_when_agent_toolkit_target`と同じくwarn降格とし、
    呼び出し元（`_check_plan_file`）はexit codeへ含めずstderr出力のみに使う
    （`agent-toolkit-edit`スキル「bump不要時のみ省略可」文言との整合を保つ）。
    """
    if has_bump_step_when_required(text):
        return None
    return (
        f"{plan_path}: [warn] 対象ファイル一覧に`agent-toolkit/`配下パスを含むが、"
        f"`## 実行方法`本文に`agent_toolkit_bump.py`ステップが記載されていない。"
        f"`agent-toolkit-edit`スキル「バージョン更新」節参照。"
    )


def _check_outer_label_placement(plan_path: pathlib.Path, text: str) -> list[str]:
    """`## 変更内容`本文でfence外側配置ラベルと全角化ラベルを検出する。

    検出パターンは次の2種類。いずれもexit 1違反として`stderr`へ列挙する。

    1. `[現行]`／`[置換後]`ラベル文言単独の行の後に、空行が0行以上あって
        textフェンス開始行が続く場合（fence外側配置。
        plan-file-diff-labels.md「フェンス配置」節の禁止規定の機械強制）
    2. 全角化`[現行］`／`[置換後］`がtextフェンス外側行へ出現する場合
        （textlint autofixによる二次被害。フェンス内側のコードブロック区間は
        textlintのautofix対象外のため全角化自体が発生しない）

    fenceステート追跡でtextフェンス内側を検出対象から除外する
    （テストフィクスチャ等でフェンス内側にラベル単独行が正当に埋め込まれるケースを誤検出しないため）。
    """
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return []
    violations: list[str] = []
    lines = section.splitlines()
    n = len(lines)
    open_marker: str | None = None
    for i, line in enumerate(lines):
        if open_marker is None:
            m_open = TEXT_FENCE_OPEN_RE.match(line)
            if m_open:
                open_marker = m_open.group(1)
                continue
            # 外側のみ検査対象
            if _FULLWIDTH_LABEL_RE.search(line):
                absolute_line = section_start_line + i + 1
                msg = (
                    f"{plan_path}:{absolute_line}: `## 変更内容`本文で全角化ラベル"
                    f"を検出。半角へ修正する（plan-file-diff-labels.md「フェンス配置」節参照）。"
                )
                print(msg, file=sys.stderr)
                violations.append(msg)
                continue
            if _OUTER_LABEL_LINE_RE.match(line):
                for j in range(i + 1, n):
                    next_line = lines[j]
                    if not next_line.strip():
                        continue
                    if TEXT_FENCE_OPEN_RE.match(next_line):
                        absolute_line = section_start_line + i + 1
                        msg = (
                            f"{plan_path}:{absolute_line}: fence外側配置の"
                            f"ラベルを検出。fence直後1行目に配置する"
                            f"（plan-file-diff-labels.md「フェンス配置」節参照）。"
                        )
                        print(msg, file=sys.stderr)
                        violations.append(msg)
                    break
        else:
            if is_matching_close(open_marker, line):
                open_marker = None
    return violations


def _check_manifest_files_when_bump_step(plan_path: pathlib.Path, text: str) -> str | None:
    """計画ファイル本文へmanifest対象ファイル記載要件を照合する。違反時は警告メッセージを返す。

    判定ロジックのSSOTは`_plan_format.has_manifest_files_when_bump_step_present`。
    既存`_check_bump_step`と対称のwarn降格とし、呼び出し元はexit codeへ含めずstderr出力のみに使う
    （`agent-toolkit-edit`スキル「バージョン更新」節との整合を保つ）。
    """
    if has_manifest_files_when_bump_step_present(text):
        return None
    return (
        f"{plan_path}: [warn] `## 実行方法`本文にbump stepが記載されているが、"
        f"対象ファイル一覧に両manifestの記載が欠落している。"
        f"`agent-toolkit-edit`スキル「バージョン更新」節参照。"
    )


def _check_retroactive_scan_when_new_norm_section(plan_path: pathlib.Path, text: str) -> str | None:
    """規範ファイルへの新規##節追加を含む計画では、遡及スキャン結果小見出しの存在を検査する。

    判定条件:

    - `## 変更内容 > ### 対象ファイル一覧`に`agent-toolkit/rules/*.md`または
      `agent-toolkit/skills/*/references/*.md`のパスを1件以上含む
    - `## 変更内容`本文の`_extract_diff_blocks`が返す差分ブロックのbodyに`^## `H2見出し行を含む
    - `## 調査結果`配下に`### 遡及スキャン結果`小見出しが存在しない

    3条件全て成立時に`_check_bump_step`同型のwarn分類警告を返す。
    呼び出し元はexit codeへ含めずstderr出力のみに用いる。
    既存の`_check_plan_file_retroactive_scan_recorded`(`pretooluse.py`側)は
    「規範ドキュメント側の編集Write時」を検査起点とするため、本checkは計画本文Write時に検査を前倒しする。
    """
    target_files = extract_target_files_from_changes(text)
    if not any(is_agent_doc_target_file(p) for p in target_files):
        return None
    has_new_heading = False
    for _, _, body, _ in _iter_diff_blocks(text):
        if _NEW_NORM_HEADING_RE.search(body):
            has_new_heading = True
            break
    if not has_new_heading:
        return None
    survey_body, _ = extract_section_with_offset(text, "## 調査結果")
    if survey_body is not None and _RETROACTIVE_SCAN_HEADING_RE.search(survey_body):
        return None
    return (
        f"{plan_path}: [warn] 対象ファイル一覧に規範ファイルを含み、"
        f"かつ`## 変更内容`本文で新規節見出しの追加を検出したが、"
        f"`## 調査結果`配下に`### 遡及スキャン結果`小見出しが存在しない。"
        f"`plan-mode/references/norm-revision-checklist.md`「規範対象範囲の網羅確認」節参照。"
    )


def _iter_diff_blocks(text: str) -> Iterator[tuple[str, int, str, str]]:
    """計画ファイル本文から検査対象ブロックを`(H3ラベル, ブロック開始行番号, ブロック本文, ファイル拡張子)`で順に返す。

    `## 変更内容`セクションに限定して走査する。H3見出しの走査状態を更新しつつ`text`フェンスを検出する。
    各フェンスについて、フェンス直後1行目（fence内側）のラベル判定・トリガー継続中フラグ・
    見出しコンテキストで検査対象かを判断する。ファイル拡張子はH3見出し内のバッククォート付きファイル名から
    抽出し、`_check_plan_file`側で散文系lint（textlint）の適用可否判定に使う。
    """
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return
    lines = section.splitlines()
    n = len(lines)
    current_h3: str = ""
    current_ext: str = ""
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
            current_ext = _extract_h3_ext(rest)
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
                yield (current_h3, absolute_line, body, current_ext)
            continue

        stripped = line.strip()
        if stripped and any(token in stripped for token in _ADDITION_TRIGGER_TOKENS):
            trigger_active = True
        i += 1


def _extract_h3_ext(rest: str) -> str:
    """H3見出し本文からバッククォート付きファイル名の拡張子を抽出する。

    `.md.tmpl`は複合拡張子として1トークン扱いとする。バッククォート付きファイル名が無い場合は空文字を返す。
    """
    m = _H3_FILE_RE.search(rest)
    if not m:
        return ""
    name = m.group(1)
    if name.endswith(".md.tmpl"):
        return ".md.tmpl"
    return pathlib.PurePosixPath(name).suffix


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


def _extract_diff_blocks(
    plan_path: pathlib.Path,
) -> tuple[list[str], tuple[list[pathlib.Path], list[pathlib.Path]]]:
    """統合ランナー向けに1計画ファイルを走査し、`(違反メッセージ一覧, (textlint対象, line-width対象))`を返す。

    fence外側配置検査・縮退フレーズ検査・bump/manifest/遡及スキャン警告はここで実行して
    メッセージまたは`stderr`出力へ反映する。各対象ブロック本文は一時ファイル（`.md`拡張子）へ
    保存してパスのみ返し、textlint・line-width検査自体は`_check_extracted_paths`へ委譲する
    （1計画ファイル分の全ブロックをまとめて1回のsubprocess呼び出しへ渡すため）。

    textlint対象は`.md`・`.md.tmpl`ブロック（`_PROSE_EXTENSIONS`）に限定する。
    `.py`等のコードブロックへtextlintを適用すると日本語文体ルールが偽陽性検出するため。
    line-width検査（127幅）は言語非依存のため全ブロックへ適用する。
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{plan_path}: 計画ファイルの読み込みに失敗 ({exc})"
        print(msg, file=sys.stderr)
        return [msg], ([], [])

    messages: list[str] = list(_check_outer_label_placement(plan_path, text))
    prose_paths: list[pathlib.Path] = []
    line_width_paths: list[pathlib.Path] = []
    for h3_label, block_start_line, body, h3_ext in _iter_diff_blocks(text):
        category = _run_scope_escalation(body)
        if category is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` 縮退フレーズ検出（カテゴリ: {category}）"
            print(msg, file=sys.stderr)
            messages.append(msg)
        if body:
            tmp_path = _write_tmpfile(body)
            line_width_paths.append(tmp_path)
            if h3_ext in _PROSE_EXTENSIONS:
                prose_paths.append(tmp_path)

    bump_warning = _check_bump_step(plan_path, text)
    if bump_warning is not None:
        print(bump_warning, file=sys.stderr)
    manifest_warning = _check_manifest_files_when_bump_step(plan_path, text)
    if manifest_warning is not None:
        print(manifest_warning, file=sys.stderr)
    retroactive_scan_warning = _check_retroactive_scan_when_new_norm_section(plan_path, text)
    if retroactive_scan_warning is not None:
        print(retroactive_scan_warning, file=sys.stderr)

    return messages, (prose_paths, line_width_paths)


def _write_tmpfile(body: str) -> pathlib.Path:
    """検査対象ブロック本文を`.md`拡張子の一時ファイルへ保存し、パスを返す。"""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md", delete=False) as tmp:
        tmp.write(body)
        return pathlib.Path(tmp.name)


def _check_extracted_paths(
    paths: tuple[list[pathlib.Path], list[pathlib.Path]],
) -> list[str]:
    """`_extract_diff_blocks`が抽出した一時ファイル群へtextlint・`check_line_width.py`を1回ずつ実行する。

    `paths`は`(textlint対象, line-width対象)`の2要素タプル。textlint対象は散文系拡張子
    （`.md`・`.md.tmpl`）に限定した部分集合で、line-width対象は全ブロックを含む。
    呼び出し元（統合ランナー）は返り値メッセージをそのまま`stderr`へ出力する
    （本関数自体はstderrへ直接出力せず戻り値のみで結果を返す。ただしsubprocess呼び出しと
    一時ファイル削除の副作用は伴う）。
    """
    prose_paths, line_width_paths = paths
    all_paths = list({p: None for p in [*prose_paths, *line_width_paths]}.keys())
    if not all_paths:
        return []
    try:
        messages: list[str] = []
        if prose_paths:
            textlint_error = _run_textlint_batch(prose_paths)
            if textlint_error is not None:
                messages.append(f"textlint違反\n{textlint_error}")
        if line_width_paths:
            line_width_error = _run_line_width_batch(line_width_paths)
            if line_width_error is not None:
                messages.append(f"line-width違反\n{line_width_error}")
        return messages
    finally:
        for path in all_paths:
            path.unlink(missing_ok=True)


def _run_textlint_batch(paths: list[pathlib.Path]) -> str | None:
    """一時ファイル群へtextlintを1回のsubprocess呼び出しで実行し、違反時stderr内容・未違反時Noneを返す。"""
    result = subprocess.run(
        ["uvx", "pyfltr", "run-for-agent", "--commands=textlint", "--no-fix", *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    combined = (result.stdout or "") + (result.stderr or "")
    return combined.strip() or f"textlint exit={result.returncode}"


def _run_line_width_batch(paths: list[pathlib.Path]) -> str | None:
    """一時ファイル群へ`check_line_width.py`を1回のsubprocess呼び出しで実行し、違反時stderr内容・未違反時Noneを返す。"""
    result = subprocess.run(
        [sys.executable, str(_CHECK_LINE_WIDTH_CLI), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    combined = (result.stdout or "") + (result.stderr or "")
    return combined.strip() or f"check_line_width exit={result.returncode}"


if __name__ == "__main__":
    sys.exit(main())
