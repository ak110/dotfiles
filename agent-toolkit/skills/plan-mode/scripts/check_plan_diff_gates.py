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
- `[追記]`単独ラベル配下のtextフェンスブロック
- 追記/縮減トリガー文（「追記文言案」「追記内容:」「追記:」「追加:」「圧縮対象:」「圧縮後:」等）配下の直後textフェンスブロック
- `（新設）`H3配下のtextフェンスブロック
- `#### 縮減対象`小見出し配下のtextフェンスブロック

`[現行]`ラベル配下のブロックは削除予定の既存文言のため対象外とする。

兄弟スクリプト`check_wc_projection.py`は`[現行]`/`[置換後]`対比と`[削除根拠]`および
`「追記文言案」`トリガー・`#### 縮減対象`見出しを追記/縮減の行数集計対象とする。
本スクリプトのトリガー語彙は事前機械検査（縮退フレーズ・textlint）を
広く発火させる目的でwc集計側より広く定義しており、両者の対象範囲は意図的に非対称である。

各対象ブロック本文を次の検査へ通し、違反があれば`stderr`へ列挙してexit 1で終了する。
無違反時はexit 0で終了する。

- `## 変更内容`本文のfence外側配置ラベルおよび全角化ラベルの全文一括検査（exit 1違反）
- 縮退フレーズ検出: `agent-toolkit/scripts/_scope_escalation.py` CLI（stdin→exit 2で一致）。
    フェンス直前の非フェンス行に抑止マーカー`<!-- scope-escalation-ok -->`を配置すると、
    直後のフェンス1個分の本検査を抑止する（新設カテゴリの代表フレーズ実例を
    `[置換後]`ブロックへ含める場合の自己言及誤検出を回避する目的）
- textlint併走colloquial-check: 一時ファイル拡張子を`.md`に固定して
    `uvx pyfltr run-for-agent --commands=textlint,colloquial-check --enable=colloquial-check --no-fix <tmpfile.md>`
    を呼び出す。フェンス内文面へ計画段階でcolloquial-checkを到達させるための併走。
    体裁系のため警告出力のみでexit codeへ算入しない
- 対象ファイル一覧の`agent-toolkit/`配下パスに対するversion bumpステップ欠落の全文一括検査（warn出力のみ）
- `## 実行方法`にbump stepが記載されている場合のmanifest対象ファイル記載欠落の全文一括検査（warn出力のみ）
- 対象ファイル一覧に絶対パスまたは親ディレクトリ参照（`..`を含むパス）を検出した場合の全文一括検査（warn出力のみ）
- 対象ファイル一覧に規範ファイル（`agent-toolkit/rules/*.md`等）を含み、`## 変更内容`本文の
    差分ブロックで新規H2以深節見出しの追加を検出したが`## 調査結果`配下に`### 遡及スキャン結果`
    小見出しが存在しない場合の全文一括検査（warn出力のみ）
- 「同構造」「同旨」「同期」宣言表現検出時の対象ファイル本体との整合性検査（warn出力のみ）

SSOTコメント: 共通トークンは兄弟モジュール`_plan_diff_parsing.py`へ集約済みでありimportで参照する。
意味論差異の温存方針は`_plan_diff_parsing.py`のdocstring参照。
frontmatterサブラベル（`[追記（frontmatter）]`等4種）は`FRONTMATTER_LABEL_RE`の完全一致で
`_classify_block`・`_is_label_line`双方が本体ラベルと同じ種別へ分類する。

統合ランナー`check_plan_file.py`向けに、差分ブロック抽出とtextlint起動の責務を分離した
`_extract_diff_blocks(plan_path)`・`_check_extracted_paths(paths)`も公開する。抽出済み一時ファイル群を
まとめてtextlint 1回のsubprocess呼び出しへ渡し、多重起動を避ける。
単独CLI実行（`main()`経由の`_check_plan_file`）のtextlintはブロック単位のsubprocess起動のまま
変更しないが、line-width検査の呼び出しは削除する。
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
import check_deprecated_identifier_coverage  # noqa: E402
from _plan_diff_parsing import (  # noqa: E402
    FRONTMATTER_LABEL_RE,
    REDUCTION_HEADING_RE,
    TEXT_FENCE_OPEN_RE,
    extract_section_with_offset,
    is_matching_close,
)
from _plan_format import (  # noqa: E402
    extract_target_files_from_changes,
    find_invalid_target_file_paths,
    has_bump_step_when_required,
    has_manifest_files_when_bump_step_present,
    has_recurrence_prevention_when_section_present,
    is_agent_doc_target_file,
)

# pylint: enable=wrong-import-position

# `agent-toolkit/scripts/_scope_escalation.py`の絶対パス。
# 本スクリプトは`agent-toolkit/skills/plan-mode/scripts/`配下のため、3階層遡って`scripts/`へ到達する。
_SCOPE_ESCALATION_CLI = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "_scope_escalation.py"

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
_ADDITION_LABEL_TOKEN = "[追記]"

# ラベル行判定用トークン一覧（fence直後1行目から本文抽出時に除外する対象）。
_ALL_LABEL_TOKENS = (
    _NEW_LABEL_TOKEN,
    _REPLACEMENT_LABEL_TOKEN,
    _REPLACEMENT_FULL_LABEL_TOKEN,
    _CURRENT_LABEL_TOKEN,
    _DELETION_RATIONALE_LABEL_TOKEN,
    _ADDITION_LABEL_TOKEN,
)

# `FRONTMATTER_LABEL_RE`のキャプチャグループ1（角括弧・「（frontmatter）」を除いたトークン）から
# `_classify_block`の戻り値種別へ変換するマップ。本体ラベルトークン（角括弧付き）とは異なり
# 角括弧なしで比較するため、専用の対応表として保持する。
_FRONTMATTER_LABEL_TOKEN_TO_KIND: dict[str, str | None] = {
    "現行": None,
    "削除根拠": None,
    "置換後": "replacement",
    "追記": "addition",
}

# 追記/縮減トリガー文に含まれるトークン（フェンス直前非空行に部分一致する場合、次のtextブロックを検査対象へ加える）。
_ADDITION_TRIGGER_TOKENS = ("追記文言案", "追記内容:", "追記:", "追加:", "圧縮対象:", "圧縮後:")

# `（新設）`注記付きH3見出しの判定トークン。
_NEW_H3_MARKER = "（新設）"

# fence外側配置検出用: ラベル文言単独行（前後空白のみ許容）。
_OUTER_LABEL_LINE_RE = re.compile(r"^\s*(?:\[現行\]|\[置換後\])\s*$")

# 縮退フレーズ検出ゲートの抑止マーカー。フェンス直前の非フェンス行に配置すると直後の
# textフェンス1個分の`_run_scope_escalation`検査を抑止する。新設カテゴリの代表フレーズ実例を
# `[置換後]`ブロックへ含める場合（自己言及で誤検出するケース）に用いる。
_SCOPE_ESCALATION_ALLOW_MARKER = "<!-- scope-escalation-ok -->"

# 全角化ラベル検出用: textlint autofixで閉じ括弧が全角化された`[現行］`／`[置換後］`。
_FULLWIDTH_LABEL_RE = re.compile(r"(?:\[現行］|\[置換後］)")

# fb-3: 新規H2以深節見出し検出用。`pretooluse.py:2297`付近の
# `_RETROACTIVE_SCAN_NEW_HEADING_PATTERN`と同じ正規表現を採用する。
_NEW_NORM_HEADING_RE = re.compile(r"^##[#]* .+$", re.MULTILINE)

# fb-3: `### 遡及スキャン結果`小見出し検出用。
_RETROACTIVE_SCAN_HEADING_RE = re.compile(r"^###\s+遡及スキャン結果\s*$", re.MULTILINE)

# fb-4: 「同構造」「同旨」「同期して」宣言表現検出用。
_TRANSCRIPTION_DECLARATION_RE = re.compile(r"同構造|同旨|同期して")

# fb-4: 転記先ファイル本体との矛盾有無を確認するキーワード一覧。
_TRANSCRIPTION_CONFLICT_KEYWORDS = ("push", "git commit", "git push", "レビュー", "作業ツリー")

# fb-4: キーワード出現行の前後windowに含まれる場合、既存規定側の否定文脈と判定するトークン。
# 「ない」は「しない」「行わない」等の動詞否定形を広く捕捉する意図的に緩い判定とする
# （本チェックはwarn出力のみでexit codeへ影響しないため、過検出より見落とし回避を優先する）。
_NEGATION_CONTEXT_RE = re.compile(r"ない|対象外")

# fb-4: キーワード出現行を中心とした前後の走査幅（行数）。
_NEGATION_CONTEXT_WINDOW = 3

# fb-4: `### エージェント判断`H3見出し検出用。
_AGENT_JUDGMENT_HEADING_RE = re.compile(r"^###\s*エージェント判断\s*$")

# fb-4: 責務差分表の記入検出用（`### エージェント判断`配下の見出し行に対して判定する）。
_RESPONSIBILITY_DIFF_HEADING_RE = re.compile(r"^#{1,6}\s*.*責務差分")


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

    # 統合ランナー側の既存慣例と同じく、兄弟スクリプトのリポジトリルート解決を再利用する。
    repo_root = check_deprecated_identifier_coverage._find_repo_root(pathlib.Path.cwd())  # pylint: disable=protected-access
    total_violations = 0
    for plan_path in args.plan_paths:
        total_violations += len(_check_plan_file(plan_path, repo_root))
    return 1 if total_violations > 0 else 0


def _check_plan_file(plan_path: pathlib.Path, repo_root: pathlib.Path) -> list[str]:
    """1計画ファイルを走査し、違反メッセージ一覧を返す。副作用として`stderr`へも出力する。

    `repo_root`は`_check_transcription_declaration_consistency`の対象ファイル解決に用いる
    （`check_line_ref.py`等の兄弟モジュールと同様、呼び出し側で算出した値を明示的に受け取る）。
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{plan_path}: 計画ファイルの読み込みに失敗 ({exc})"
        print(msg, file=sys.stderr)
        return [msg]

    violations: list[str] = []
    violations.extend(_check_outer_label_placement(plan_path, text))
    scope_escalation_allowed_starts = _scope_escalation_allowed_starts(text)
    for h3_label, block_start_line, body, h3_ext in _iter_diff_blocks(text):
        category = None if block_start_line in scope_escalation_allowed_starts else _run_scope_escalation(body)
        if category is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` 縮退フレーズ検出（カテゴリ: {category}）"
            print(msg, file=sys.stderr)
            violations.append(msg)
        # textlint違反は体裁系のため警告出力のみとしviolationsへは加算しない
        # （縮退フレーズ検査のみ構造系としてviolationsへ加算する）。
        if h3_ext in _PROSE_EXTENSIONS:
            textlint_error = _run_textlint(body)
            if textlint_error is not None:
                msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` textlint違反（警告・非ブロック）\n{textlint_error}"
                print(msg, file=sys.stderr)
    bump_warning = _check_bump_step(plan_path, text)
    if bump_warning is not None:
        print(bump_warning, file=sys.stderr)
    recurrence_error = _check_recurrence_prevention_recorded(plan_path, text)
    if recurrence_error is not None:
        print(recurrence_error, file=sys.stderr)
        violations.append(recurrence_error)
    manifest_warning = _check_manifest_files_when_bump_step(plan_path, text)
    if manifest_warning is not None:
        print(manifest_warning, file=sys.stderr)
    target_path_warning = _check_target_file_paths_relative(plan_path, text)
    if target_path_warning is not None:
        print(target_path_warning, file=sys.stderr)
    retroactive_scan_warning = _check_retroactive_scan_when_new_norm_section(plan_path, text)
    if retroactive_scan_warning is not None:
        print(retroactive_scan_warning, file=sys.stderr)
    for transcription_warning in _check_transcription_declaration_consistency(plan_path, text, repo_root):
        print(transcription_warning, file=sys.stderr)
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


def _check_recurrence_prevention_recorded(plan_path: pathlib.Path, text: str) -> str | None:
    """`### 恒久化・リファクタリング内容`小見出しの再発予防記述要件を照合する。

    判定ロジックのSSOTは`_plan_format.has_recurrence_prevention_when_section_present`。
    `_check_bump_step`と異なりexit codeへ含める（ユーザー指示による機械ゲート化）。
    """
    if has_recurrence_prevention_when_section_present(text):
        return None
    return (
        f"{plan_path}: `### 恒久化・リファクタリング内容`小見出し配下に再発予防の検討結果が"
        f"含まれていない。`agent-toolkit:plan-mode`工程4「恒久化検討」参照。"
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


def _check_target_file_paths_relative(plan_path: pathlib.Path, text: str) -> str | None:
    """対象ファイル一覧のパス表記がプロジェクトルート相対であるかを照合する。違反時は警告メッセージを返す。

    判定ロジックのSSOTは`_plan_format.find_invalid_target_file_paths`。
    既存`_check_bump_step`と対称のwarn降格とし、呼び出し元はexit codeへ含めずstderr出力のみに使う。
    `skills/plan-mode/references/plan-file-guidelines.md`「計画ファイル全体の遵守事項」節参照。
    """
    invalid = find_invalid_target_file_paths(text)
    if not invalid:
        return None
    joined = ", ".join(f"`{p}`" for p in invalid)
    return (
        f"{plan_path}: [warn] `## 変更内容 > ### 対象ファイル一覧`に"
        f"絶対パスまたは親ディレクトリ参照を含む項目を検出: {joined}。"
        f"プロジェクトルート相対の完全パスへ修正する"
        f"（`skills/plan-mode/references/plan-file-guidelines.md`参照）。"
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


def _check_transcription_declaration_consistency(plan_path: pathlib.Path, text: str, repo_root: pathlib.Path) -> list[str]:
    """「同構造」「同旨」「同期して」宣言による転記時、対象ファイル本体との責務差分の見落としを検査する。

    `repo_root`は対象ファイルパスの解決起点。`check_line_ref.py`と同様に呼び出し側
    （`check_plan_file.py`または本モジュールの`main()`）が算出した値を明示的に受け取る。

    `## 変更内容`H3配下の地の文から`_TRANSCRIPTION_DECLARATION_RE`（「同構造」「同旨」「同期して」）を
    検出する。判定対象キーワードは`_TRANSCRIPTION_CONFLICT_KEYWORDS`（`push`・`git commit`・`git push`・
    `レビュー`・`作業ツリー`）の全件ではなく、同H3配下の追記/置換ブロック本文
    （`_iter_diff_blocks`抽出結果。`[現行]`・`[削除根拠]`ラベル配下の既存文言・削除説明は
    `_iter_diff_blocks`が既に対象外とするため自然と追記・置換後・新設内容へ限定される）に
    実際に出現する要素のみへ限定する。無関係な既存の否定文脈を誤検出しないための限定である。
    限定後のキーワードが検出したH3見出し（`### <ファイルパス>`）の指す対象ファイル本体で
    否定文脈（「〜しない」「対象外」等）に使われていないかを前後`_NEGATION_CONTEXT_WINDOW`行の
    windowで検査する。否定文脈での使用を検出した場合はwarnメッセージを返す
    （`_check_plan_file`はexit codeへ含めない）。
    `### エージェント判断`配下に「責務差分表」または「責務差分」の見出しが存在する場合は
    責務差分の記入済みとみなし、本チェック全体のwarn出力を抑制する。
    `check_wc_projection.py`とは異なり本関数はpure Python正規表現のみで完結し、
    subprocess呼び出しは行わない。
    """
    if _has_responsibility_diff_table(text):
        return []
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return []

    h3_keywords = _collect_transcription_block_keywords(text)

    warnings: list[str] = []
    lines = section.splitlines()
    current_files: list[str] = []
    current_h3 = ""
    reported: set[tuple[str, str]] = set()
    for idx, line in enumerate(lines):
        m_h3 = _H3_RE.match(line)
        if m_h3:
            current_h3 = m_h3.group("rest").strip()
            matches = _H3_FILE_RE.findall(m_h3.group("rest"))
            if matches:
                current_files = matches
            else:
                # バッククォートなしH3見出しをフォールバック抽出
                # （SSOT plan-file-diff-labels.md規定の標準形式に対応）。
                # `_NEW_H3_MARKER`注記が末尾に付く場合は除去する。
                candidate = current_h3
                if candidate.endswith(_NEW_H3_MARKER):
                    candidate = candidate[: -len(_NEW_H3_MARKER)].strip()
                current_files = [candidate] if candidate else []
            continue
        if not _TRANSCRIPTION_DECLARATION_RE.search(line):
            continue
        keywords_in_body = h3_keywords.get(current_h3, frozenset())
        if not keywords_in_body:
            continue
        for target_rel in current_files:
            target_text = _read_target_text_or_none(target_rel, repo_root)
            if target_text is None:
                continue
            for keyword in keywords_in_body:
                if (target_rel, keyword) in reported:
                    continue
                if not _keyword_used_in_negated_context(target_text, keyword):
                    continue
                reported.add((target_rel, keyword))
                absolute_line = section_start_line + idx + 1  # `extract_section_with_offset`の1始まり換算規約
                warnings.append(
                    f"{plan_path}:{absolute_line}: [warn] 対象ファイル`{target_rel}`に"
                    f"既存規定と矛盾する可能性がある（キーワード: `{keyword}`）。"
                    f"責務差分表を`### エージェント判断`へ記入するか、追記文言を分離すること。"
                )
    return warnings


def _collect_transcription_block_keywords(text: str) -> dict[str, frozenset[str]]:
    """H3見出しごとに、追記/置換ブロック本文へ実際に出現する転記競合キーワード集合を収集する。

    `_iter_diff_blocks`が返す本文（`[現行]`・`[削除根拠]`ラベル配下は既に対象外）を走査し、
    `_TRANSCRIPTION_CONFLICT_KEYWORDS`のうち各H3配下のブロック本文に出現する要素のみを集める。
    """
    result: dict[str, set[str]] = {}
    for h3_label, _block_start_line, body, _ext in _iter_diff_blocks(text):
        found = {keyword for keyword in _TRANSCRIPTION_CONFLICT_KEYWORDS if keyword in body}
        if not found:
            continue
        result.setdefault(h3_label, set()).update(found)
    return {h3_label: frozenset(keywords) for h3_label, keywords in result.items()}


def _read_target_text_or_none(rel_path: str, repo_root: pathlib.Path) -> str | None:
    """`repo_root`基準で対象ファイルを読み込む。パス不在・読み込み失敗時は`None`を返す。

    `rel_path`が絶対パスの場合は`pathlib`の結合仕様により`repo_root`を無視して
    そのまま解決される（テストフィクスチャ等が絶対パスを直接指定する経路と後方互換）。
    """
    target_path = repo_root / rel_path
    if not target_path.exists():
        return None
    try:
        return target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _keyword_used_in_negated_context(target_text: str, keyword: str) -> bool:
    """`keyword`が対象ファイル本体で否定文脈（「〜しない」「対象外」等）内に出現するかを判定する。

    出現行を中心に前後`_NEGATION_CONTEXT_WINDOW`行のwindowを走査し、
    `_NEGATION_CONTEXT_RE`に一致する行がwindow内に存在すれば否定文脈と判定する。
    """
    target_lines = target_text.splitlines()
    for idx, line in enumerate(target_lines):
        if keyword not in line:
            continue
        window_start = max(0, idx - _NEGATION_CONTEXT_WINDOW)
        window_end = min(len(target_lines), idx + _NEGATION_CONTEXT_WINDOW + 1)
        window = target_lines[window_start:window_end]
        if any(_NEGATION_CONTEXT_RE.search(w) for w in window):
            return True
    return False


def _has_responsibility_diff_table(text: str) -> bool:
    """`### エージェント判断`配下に「責務差分表」または「責務差分」の見出しが存在するかを判定する。

    区間は`### エージェント判断`見出し行から、同階層以上（`##`〜`###`）の次の見出し行までとする。
    """
    lines = text.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if _AGENT_JUDGMENT_HEADING_RE.match(stripped):
            in_section = True
            continue
        if not in_section:
            continue
        if _RESPONSIBILITY_DIFF_HEADING_RE.match(stripped):
            return True
        if re.match(r"^#{1,3}\s", stripped) and stripped != "":
            # 同階層以上の次の見出しに到達したら区間終了（責務差分見出しは既に上で判定済み）。
            break
    return False


def _scope_escalation_allowed_starts(text: str) -> frozenset[int]:
    """`_SCOPE_ESCALATION_ALLOW_MARKER`が配置された行の直後にある`text`フェンスの本文開始行集合を返す。

    `## 変更内容`セクション内でマーカーを含む非フェンス行を検出するたびフラグを立て、
    直後に出現する最初の`text`フェンス開始（`_iter_diff_blocks`が返す`block_start_line`と同じ
    行番号換算）を集合へ追加してフラグを解除する。フェンス内側の行はマーカー検出対象に含めない。
    """
    section, section_start_line = extract_section_with_offset(text, "## 変更内容")
    if section is None:
        return frozenset()
    lines = section.splitlines()
    n = len(lines)
    allowed: set[int] = set()
    marker_pending = False
    i = 0
    while i < n:
        line = lines[i]
        m_open = TEXT_FENCE_OPEN_RE.match(line)
        if m_open:
            open_marker = m_open.group(1)
            block_start = i + 1
            i += 1
            if marker_pending:
                allowed.add(section_start_line + block_start)
                marker_pending = False
            while i < n and not is_matching_close(open_marker, lines[i]):
                i += 1
            i += 1  # 閉じフェンス行を除外する
            continue
        if _SCOPE_ESCALATION_ALLOW_MARKER in line:
            marker_pending = True
        i += 1
    return frozenset(allowed)


def _iter_diff_blocks(text: str) -> Iterator[tuple[str, int, str, str]]:
    """計画ファイル本文から検査対象ブロックを`(H3ラベル, ブロック開始行番号, ブロック本文, ファイル拡張子)`で順に返す。

    `## 変更内容`セクションに限定して走査する。H3見出しの走査状態を更新しつつ`text`フェンスを検出する。
    各フェンスについて、フェンス直後1行目（fence内側）のラベル判定・トリガー継続中フラグ・
    見出しコンテキストで検査対象かを判断する。ファイル拡張子はH3見出し内のバッククォート付きファイル名から
    抽出し、`_check_plan_file`側で散文系lint（textlint）の適用可否判定に使う。
    frontmatterサブラベル（`FRONTMATTER_LABEL_RE`の完全一致）配下の本文は、
    ホストファイルの拡張子が`.md`等であっても空文字列拡張子として返しtextlint対象から除外する
    （本文がYAML/Python形式のコメント文言のため、独立抽出時にtextlintがATX見出しと誤認する）。
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
                is_frontmatter = bool(content_lines) and bool(FRONTMATTER_LABEL_RE.match(content_lines[0].strip()))
                body_lines = content_lines
                if body_lines and _is_label_line(body_lines[0]):
                    body_lines = body_lines[1:]
                body = "\n".join(body_lines)
                # 計画ファイル全体の行番号に換算する（section開始行 + section内オフセット）。
                absolute_line = section_start_line + block_start
                # frontmatterサブラベル配下の本文はYAML/Python形式のコメント文言（`#`始まり）であり、
                # 独立ファイルへ抽出するとtextlintがATX見出しと誤認するため、
                # ホストファイルの拡張子に関わらずtextlint非対象（空文字列拡張子）として返す。
                yield (current_h3, absolute_line, body, "" if is_frontmatter else current_ext)
            continue

        stripped = line.strip()
        if stripped and any(token in stripped for token in _ADDITION_TRIGGER_TOKENS):
            trigger_active = True
        i += 1


def _extract_h3_ext(rest: str) -> str:
    """H3見出し本文からファイル名の拡張子を抽出する。

    バッククォート付きファイル名を優先し、無ければH3見出し本文全体を平文パスとして扱う。
    `_NEW_H3_MARKER`注記（`（新設）`）が末尾に付く場合は除去してから拡張子を判定する。
    `.md.tmpl`は複合拡張子として1トークン扱いとする。拡張子が無い場合は空文字を返す。
    SSOT `plan-file-diff-labels.md`はバッククォートなしH3見出しを規定するため両形式に対応する。
    """
    m = _H3_FILE_RE.search(rest)
    name = m.group(1) if m else rest.strip()
    if name.endswith(_NEW_H3_MARKER):
        name = name[: -len(_NEW_H3_MARKER)].strip()
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
    1. frontmatterサブラベル（`[現行（frontmatter）]`等4種、`FRONTMATTER_LABEL_RE`の完全一致）は
       対応する本体ラベルと同じ種別へ分類する
    2. `[現行]`・`[削除根拠]`ラベル配下は既存文言または削除説明のため検査対象外（`None`）
    3. `[新設]`・`[置換後]`・`[置換後（全文）]`・`[追記]`ラベル配下は種別ラベルを返す
       （`[追記]`は`addition`）
    4. `#### 縮減対象`見出し配下は`reduction`を返す
    5. `（新設）`H3配下は`new-h3`を返す
    6. 追記トリガー文出現後で当該H3節境界に未到達なら`addition`を返す
    それ以外は検査対象外として`None`を返す。
    """
    first = content_lines[0].strip() if content_lines else ""
    if first:
        m_frontmatter = FRONTMATTER_LABEL_RE.match(first)
        if m_frontmatter:
            return _FRONTMATTER_LABEL_TOKEN_TO_KIND[m_frontmatter.group(1)]
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
        if _ADDITION_LABEL_TOKEN in first:
            return "addition"
    if in_reduction_heading:
        return "reduction"
    if in_new_h3:
        return "new-h3"
    if trigger_active:
        return "addition"
    return None


def _is_label_line(line: str) -> bool:
    """fence直後1行目が差分ラベル行に該当するかを判定する（本文抽出時の除外判定に用いる）。

    frontmatterサブラベル（`FRONTMATTER_LABEL_RE`の完全一致）も本体ラベルと同様に該当扱いとする。
    """
    stripped = line.strip()
    if FRONTMATTER_LABEL_RE.match(stripped):
        return True
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
    """一時ファイル経由でtextlintおよびcolloquial-checkを実行し、違反時stderr内容・未違反時Noneを返す。

    計画本文のフェンス内文面は上位のcolloquial-checkが検査対象外とするため、
    フェンス外扱いの一時ファイル経由でcolloquial-checkを併走させる。
    実装検証段階でのcolloquial-check違反発覚を計画段階で先取り検出する。
    呼び出し元では違反ラベルを「textlint違反」で統一する。
    colloquial-check由来の違反も同ラベルで報告される点は既知とする。
    """
    return _run_tmpfile_check(
        body,
        lambda p: [
            "uvx",
            "pyfltr",
            "run-for-agent",
            "--commands=textlint,colloquial-check",
            "--enable=colloquial-check",
            "--no-fix",
            str(p),
        ],
        "textlint",
    )


def _extract_diff_blocks(
    plan_path: pathlib.Path,
) -> tuple[list[str], tuple[list[pathlib.Path], dict[str, str]]]:
    """統合ランナー向けに1計画ファイルを走査し、抽出結果を返す。

    戻り値は`(違反メッセージ一覧, (textlint対象, 一時パス→H3位置マップ))`。

    fence外側配置検査・縮退フレーズ検査・bump/manifest/遡及スキャン警告はここで実行して
    メッセージまたは`stderr`出力へ反映する。散文系拡張子（`_PROSE_EXTENSIONS`）ブロック本文は
    一時ファイル（`.md`拡張子）へ保存してパスのみ返し、textlint検査自体は`_check_extracted_paths`へ委譲する
    （1計画ファイル分の全ブロックをまとめて1回のsubprocess呼び出しへ渡すため）。

    textlint対象は`.md`・`.md.tmpl`ブロック（`_PROSE_EXTENSIONS`）に限定する。
    `.py`等のコードブロックへtextlintを適用すると日本語文体ルールが偽陽性検出するため。
    非散文系拡張子（`.py`等）ブロックは縮退フレーズ検査のみ本文へ直接適用し、
    一時ファイル生成自体を行わない。

    位置マップは一時ファイルパス文字列を`{plan_path}: H3=<label> L<block_start_line>`形式の
    H3位置マーカーへ対応付ける（`_check_extracted_paths`が違反出力内の一時パスを元位置へ
    書き換えるために使用する。バッチ実行で失われる位置情報を復元する目的）。
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{plan_path}: 計画ファイルの読み込みに失敗 ({exc})"
        print(msg, file=sys.stderr)
        return [msg], ([], {})

    messages: list[str] = list(_check_outer_label_placement(plan_path, text))
    prose_paths: list[pathlib.Path] = []
    location_map: dict[str, str] = {}
    scope_escalation_allowed_starts = _scope_escalation_allowed_starts(text)
    for h3_label, block_start_line, body, h3_ext in _iter_diff_blocks(text):
        category = None if block_start_line in scope_escalation_allowed_starts else _run_scope_escalation(body)
        if category is not None:
            msg = f"{plan_path}:{block_start_line}: H3=`{h3_label}` 縮退フレーズ検出（カテゴリ: {category}）"
            print(msg, file=sys.stderr)
            messages.append(msg)
        if body and h3_ext in _PROSE_EXTENSIONS:
            tmp_path = _write_tmpfile(body)
            location_map[str(tmp_path)] = f"{plan_path}: H3=`{h3_label}` L{block_start_line}"
            prose_paths.append(tmp_path)

    bump_warning = _check_bump_step(plan_path, text)
    if bump_warning is not None:
        print(bump_warning, file=sys.stderr)
    recurrence_error = _check_recurrence_prevention_recorded(plan_path, text)
    if recurrence_error is not None:
        print(recurrence_error, file=sys.stderr)
        messages.append(recurrence_error)
    manifest_warning = _check_manifest_files_when_bump_step(plan_path, text)
    if manifest_warning is not None:
        print(manifest_warning, file=sys.stderr)
    target_path_warning = _check_target_file_paths_relative(plan_path, text)
    if target_path_warning is not None:
        print(target_path_warning, file=sys.stderr)
    retroactive_scan_warning = _check_retroactive_scan_when_new_norm_section(plan_path, text)
    if retroactive_scan_warning is not None:
        print(retroactive_scan_warning, file=sys.stderr)

    return messages, (prose_paths, location_map)


def _write_tmpfile(body: str) -> pathlib.Path:
    """検査対象ブロック本文を`.md`拡張子の一時ファイルへ保存し、パスを返す。"""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md", delete=False) as tmp:
        tmp.write(body)
        return pathlib.Path(tmp.name)


def _check_extracted_paths(
    paths: tuple[list[pathlib.Path], dict[str, str]],
) -> list[str]:
    """`_extract_diff_blocks`が抽出した一時ファイル群へtextlintを1回実行する。

    `paths`は`(textlint対象, 位置マップ)`の2要素タプル。位置マップは
    subprocess出力中の一時ファイルパス文字列を`{plan_path}: H3=<label> L<line>`形式のH3位置マーカーへ
    書き換えるために使用する（バッチ実行で失われる位置情報を復元し、修正対象H3を特定可能にする）。
    呼び出し元（統合ランナー）は返り値メッセージをそのまま`stderr`へ出力するが、
    体裁系（textlint）のため警告出力のみとしexit codeへは算入しない
    （本関数自体はstderrへ直接出力せず戻り値のみで結果を返す。ただしsubprocess呼び出しと
    一時ファイル削除の副作用は伴う）。
    """
    prose_paths, location_map = paths
    if not prose_paths:
        return []
    try:
        messages: list[str] = []
        textlint_error = _run_textlint_batch(prose_paths)
        if textlint_error is not None:
            messages.append(f"textlint違反\n{_rewrite_locations(textlint_error, location_map)}")
        return messages
    finally:
        for path in prose_paths:
            path.unlink(missing_ok=True)


def _rewrite_locations(output: str, location_map: dict[str, str]) -> str:
    """subprocess出力内の一時ファイルパスをH3位置マーカーへ書き換える。

    textlintの違反出力には`<tmpfile>:<line>: <message>`形式で一時ファイルパスが
    含まれる。`location_map`に登録された各一時パス文字列を元H3位置（`{plan_path}: H3=<label> L<line>`）へ
    置換することで、統合ランナー経由の違反メッセージからも修正対象H3を特定できる。
    """
    for tmp_path, location in location_map.items():
        output = output.replace(tmp_path, location)
    return output


def _run_textlint_batch(paths: list[pathlib.Path]) -> str | None:
    """一時ファイル群へtextlintおよびcolloquial-checkを1回のsubprocess呼び出しで実行し、違反時stderr内容・未違反時Noneを返す。

    計画本文のフェンス内文面は上位のcolloquial-checkが検査対象外とするため、
    フェンス外扱いの一時ファイル経由でcolloquial-checkを併走させる。
    実装検証段階でのcolloquial-check違反発覚を計画段階で先取り検出する。
    呼び出し元では違反ラベルを「textlint違反」で統一する。
    colloquial-check由来の違反も同ラベルで報告される点は既知とする。
    """
    result = subprocess.run(
        [
            "uvx",
            "pyfltr",
            "run-for-agent",
            "--commands=textlint,colloquial-check",
            "--enable=colloquial-check",
            "--no-fix",
            *(str(p) for p in paths),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    combined = (result.stdout or "") + (result.stderr or "")
    return combined.strip() or f"textlint exit={result.returncode}"


if __name__ == "__main__":
    sys.exit(main())
