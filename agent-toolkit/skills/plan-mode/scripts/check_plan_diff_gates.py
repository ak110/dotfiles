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
- `_check_label_only_fence`: `## 変更内容`H3節配下のtextフェンスで内容が
    ラベル行1行のみで終わる構成（後続の実文言フェンスが検査を素通りする構造）をwarn出力する。
    exit code非算入。

SSOTコメント: 共通トークンは兄弟モジュール`_plan_diff_parsing.py`へ集約済みでありimportで参照する。
意味論差異の温存方針は`_plan_diff_parsing.py`のdocstring参照。
frontmatterサブラベル（`[追記（frontmatter）]`等4種）は`FRONTMATTER_LABEL_RE`の完全一致で
`_classify_block`・`_is_label_line`双方が本体ラベルと同じ種別へ分類する。
差分ブロック走査系・textlintバッチ実行系の関数群と関連定数は`_plan_diff_gates_scan.py`へ分離済みで
あり本モジュールからimportで参照する（1000行超過解消のためのファイル分割。分割経緯は同モジュールの
docstring参照）。

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
import sys

# 共通モジュール読み込みのため本ファイルと同一ディレクトリおよび`agent-toolkit/scripts/`を`sys.path`へ追加する。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
# pylint: disable=wrong-import-position
import check_deprecated_identifier_coverage  # noqa: E402
from _plan_diff_gates_scan import (  # noqa: E402
    _H3_FILE_RE,
    _H3_RE,
    _NEW_H3_MARKER,
    _PROSE_EXTENSIONS,
    _is_label_line,
    _iter_diff_blocks,
    _rewrite_locations,
    _run_scope_escalation,
    _run_textlint,
    _run_textlint_batch,
    _scope_escalation_allowed_starts,
    _write_tmpfile,
)
from _plan_diff_parsing import (  # noqa: E402
    TEXT_FENCE_OPEN_RE,
    extract_h3_section_with_offset,
    extract_section_with_offset,
    is_matching_close,
)
from _plan_format import (  # noqa: E402  # pylint: disable=import-error
    extract_target_files_from_changes,
    find_invalid_target_file_paths,
    has_bump_step_when_required,
    has_manifest_files_when_bump_step_present,
    has_recurrence_prevention_when_section_present,
    is_agent_doc_target_file,
)

# pylint: enable=wrong-import-position

# fence外側配置検出用: ラベル文言単独行（前後空白のみ許容）。
_OUTER_LABEL_LINE_RE = re.compile(r"^\s*(?:\[現行\]|\[置換後\])\s*$")

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

# fb-4: 責務差分表の記入検出用（`### エージェント判断`配下の見出し行に対して判定する）。
_RESPONSIBILITY_DIFF_HEADING_RE = re.compile(r"^#{1,6}\s*.*責務差分")

# fb-4: 相互参照文言（節参照・同期・意図的重複の宣言表現）検出用。
_CROSS_REFERENCE_TRIGGER_RE = re.compile(
    r"「[^」]+」\s*節\s*に従う"
    r"|『[^』]+』\s*節\s*に従う"
    r"|`[^`]+`\s*節\s*に従う"
    r"|と同期する"
    r"|と意図的に重複させている"
    r"|と意図的に同期する"
    r"|同期注記:"
)

# fb-4: `### エージェント判断`欄内の同期注記追加要否明示の判定用。
_JUDGMENT_SYNC_NOTE_RE = re.compile(r"同期注記|意図的重複")


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
    # warn出力のみでexit codeへ含めない
    for msg in _check_label_only_fence(plan_path, text):
        print(msg, file=sys.stderr)
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


def _check_label_only_fence(plan_path: pathlib.Path, text: str) -> list[str]:
    """`## 変更内容`配下のtextフェンスで内容がラベル行1行のみで終わる構成を検出する。

    ラベル単独フェンスは`_iter_diff_blocks`のbody抽出でbodyが空となり、
    後続の実文言フェンスがtextlint併走・縮退フレーズ検査を素通りする。
    `plan-file-diff-labels.md`の`フェンス配置`規定に反する構成として警告する。
    出力書式は`{plan_path}:{line}: [warn] ラベル行のみのtextフェンス構成: 後続フェンスへ本文を分離せず
    同一フェンス内へ本文を配置してください`。
    exit code非算入。既存の`[現行]`・`[削除根拠]`・`[置換後]`ペアはいずれも本文行を持つため対象外。
    """
    messages: list[str] = []
    changes_body, changes_offset = extract_section_with_offset(text, "## 変更内容")
    if not changes_body:
        return messages
    lines = changes_body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = TEXT_FENCE_OPEN_RE.match(line)
        if not match:
            i += 1
            continue
        open_marker = match.group(1)
        fence_open_idx = i
        i += 1
        content_lines: list[str] = []
        while i < len(lines):
            if is_matching_close(open_marker, lines[i]):
                break
            content_lines.append(lines[i])
            i += 1
        if i < len(lines):
            i += 1
        if not content_lines:
            continue
        first_stripped = content_lines[0].strip()
        if not _is_label_line(first_stripped):
            continue
        rest_non_empty = [ln for ln in content_lines[1:] if ln.strip()]
        if rest_non_empty:
            continue
        absolute_line = changes_offset + fence_open_idx
        messages.append(
            f"{plan_path}:{absolute_line}: [warn] "
            "ラベル行のみのtextフェンス構成: 後続フェンスへ本文を分離せず"
            "同一フェンス内へ本文を配置してください（plan-file-diff-labels.mdの`フェンス配置`規定）"
        )
    return messages


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


def _extract_judgment_section_body(text: str) -> str:
    """`### エージェント判断`H3節本文を新API`extract_h3_section_with_offset`経由で抽出する。

    走査規約は`_plan_diff_parsing.extract_h3_section_with_offset`に委譲する
    （H2境界前提の`extract_section_with_offset`との混同を防ぐ）。
    """
    body, _ = extract_h3_section_with_offset(text, "### エージェント判断")
    return body


def _has_responsibility_diff_table(text: str) -> bool:
    """`### エージェント判断`H3節配下に責務差分見出しが存在するかを判定する。"""
    section_body = _extract_judgment_section_body(text)
    return any(_RESPONSIBILITY_DIFF_HEADING_RE.match(line.strip()) for line in section_body.splitlines())


def _check_cross_reference_sync_note_requested(plan_path: pathlib.Path, text: str) -> list[str]:
    """相互参照文言検出時の`### エージェント判断`欄内の同期注記追加要否明示を照合する。

    `## 変更内容`配下の`text`フェンス本文を対象とする。
    走査は既存`_iter_diff_blocks`を直接利用する（同関数は`## 変更内容`配下に限定済み。
    `## 背景`・`### 却下した代替案`・`### 提示素材`は自然に対象外となる）。
    `### エージェント判断`節の抽出は`_extract_judgment_section_body`でH3境界に区切り、
    後続H3節（`### 却下した代替案`等）の文言混入によるfalse negativeを回避する。
    警告出力のみでexit codeへは算入しない。
    """
    messages: list[str] = []
    judgment_section = _extract_judgment_section_body(text)
    has_judgment_note = bool(_JUDGMENT_SYNC_NOTE_RE.search(judgment_section))
    if has_judgment_note:
        return messages
    for h3_label, block_start_line, body, h3_ext in _iter_diff_blocks(text):
        if h3_ext not in _PROSE_EXTENSIONS:
            continue
        if not _CROSS_REFERENCE_TRIGGER_RE.search(body):
            continue
        messages.append(
            f"{plan_path}:{block_start_line}: H3=`{h3_label}` 相互参照文言を検出したが"
            f"`### エージェント判断`欄に同期注記追加要否の明示が不在"
        )
    return messages


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


if __name__ == "__main__":
    sys.exit(main())
