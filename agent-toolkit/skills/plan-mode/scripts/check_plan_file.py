#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyfltr>=3.14.1", "platformdirs>=4.0"]
# ///
"""計画ファイル向け機械チェックの統合ランナー。

`agent-toolkit:plan-mode`の書き込み後チェック（計画ファイル本体作成直後）・
`agent-toolkit:plan-file-creator`の整合性チェック時のセルフチェック・
実装後検証の各契機から、1回のプロセス起動で下記検査を実行する。個別スクリプトを都度起動する運用は
サブプロセス起動段数・出力量が累積してコンテキストを汚染するため、本スクリプトへ統合する。

実行する検査は`error`区分と`warning`区分に大別する。

`error`区分（exit codeへ算入。計画が成立しない致命的な問題を検出する）:

- `check_wc_projection._check_wc`: `[現行]/[置換後]`対比ブロックの対象ファイル実体との一意一致検出（転記の陳腐化防止）
- `check_plan_diff_gates._extract_diff_blocks`・`_check_extracted_paths`のうち差分ブロック抽出と
  縮退フレーズ検査部分
- `check_plan_diff_gates._check_recurrence_prevention_recorded`:
  `### 恒久化・リファクタリング内容`小見出し配下の再発予防記述要件の照合（ユーザー指示による機械ゲート化）
- `check_deprecated_identifier_coverage._check_plan`: `#### 廃止・改名対象一覧`存在時の残存参照照合
- `check_line_ref._check_file`・`_check_content_level_violations`: 行番号参照・パス実在・
  スキル名実在・節名実在（パス付き節名参照形式および裸参照形式）の検査
- `check_self_ref._check_file`: 自己参照曖昧候補・禁止形式候補の検査
- `check_plan_meta._check_file`: `## 背景`配下`### 計画メタ情報`H3と起動経路・対象リポジトリ2項目の欠落検査
- `_check_version_bump_matrix`: `## 変更内容`対象ファイル一覧が`agent-toolkit/`配下の`.md`ファイルを
  含む計画で、`ファイル・改訂節数・節名・判定・該当基準`の5列表または`scripts/agent_toolkit_bump.py`の
  種別記載のいずれかが計画本文に存在するかを検査する（不在時に違反として報告）
- `_check_run_method_script_paths`: `## 実行方法`節内のバッククォート囲みコマンドから
  拡張子付き（`.py`・`.sh`・`.ps1`・`.js`・`.ts`）スクリプトパスを抽出し、
  プロジェクトルート起点で実在するかを検査する（不在時に違反として報告）
- `_check_frontmatter_sync_note_coverage`: `## 変更内容`対象ファイル一覧の各ファイルが冒頭に
  frontmatter同期注記を持つ場合、参照先ファイル・参照先節が計画本文の対象ファイル一覧・
  追記記述に含まれるかを検査する（不在時に違反として報告。ただし対象ファイル固有のH3配下スコープに
  現れない参照は無関係な既存同期注記とみなし`warning`区分へ格下げしexit codeへ算入しない）
- `_check_reduction_block_text_fence`: `#### 縮減対象`H4節配下に削除文言案の`text`コードブロックが
  存在するかを検査する（欠落時に違反として報告）

`warning`区分（exit codeへ算入しない。体裁・表記系および計画作成の往復削減方針で非ブロック化する項目）:

- `check_plan_diff_gates._extract_diff_blocks`・`_check_extracted_paths`のうちtextlint部分。
  差分ブロックへの体裁・表記系検査。体裁・表記系は計画作成の往復削減方針により非ブロック化する
- `check_plan_diff_gates._check_transcription_declaration_consistency`: 「同構造」「同旨」「同期」
  宣言表現検出時の対象ファイル本体との整合性検査
- `writing-standards/scripts/check_dash.py`: 和文ハイフン検査（サブプロセス、ファイル単位）
- `_check_document_size_upper_limit`: 現行行数が縮減計画トリガー（200行）を超える対象`.md`ファイルについて、
  対応する`#### 縮減対象`H4見出しまたは追記量圧縮の記述が計画本文に存在するかを検査する
  （体裁・構成寄りの指摘のため`warning`区分）
- `_check_test_file_pairing`: 対象ファイル一覧の`.py`実装ファイルに対応する`<basename>_test.py`が
  リポジトリに実在するのに対象ファイル一覧から欠落していないかを検査する
- `_check_version_bump_matrix_consistency`: 版更新マトリクスの改訂節数・該当基準列と判定列の整合、
  および`## 実行方法`のbump種別と判定列最大値の整合を検査する（不一致時に警告）

`warning`区分の判定根拠は次のとおり。textlint・markdownlint・typos・口語表現の全文検査は
plan-file-creatorの整合性チェックステップ4の`uvx pyfltr run-for-agent`実行（本ランナー外）でのみ行い、
本ランナー内では警告出力に留める。
体裁・表記系は`agent-toolkit:plan-mode`の「計画作成プロセス自体」改訂方針で往復削減対象とする方針に沿う。
文書サイズ上限検査・テストペア検査も即座に計画不成立にならない体裁・構成寄りの指摘として警告扱いとする。
設計原則は`agent-toolkit:agent-standards`配下`references/check-script-design.md`の
error・warning区分節を参照する。

成功時（`error`区分0違反）はexit 0で終了する。`warning`区分のみ違反時もexit 0で終了し、
警告として`stderr`へ出力する。`error`区分の違反検出時は検査名ごとに要点を`stderr`へ集約してexit 1で
終了する。`uvx pyfltr`のJSONL出力はヘッダ行・succeeded系サマリー行を含み冗長なため生出力を
転記せず、diagnostics保有行と失敗系command行のみ`file:line: message`形式または
`[pyfltr] <command>: <status> <message>`形式へ要約する
（出力ノイズ削減が本統合ランナー導入の主目的の一つ）。

import再利用する下位検査関数（`check_wc_projection._check_wc`・
`check_plan_diff_gates._extract_diff_blocks`・`check_deprecated_identifier_coverage._check_plan`）は
違反0件でもstderrへ情報出力・warn出力する副作用を持つ。本ランナーは検査単位で
`contextlib.redirect_stderr`により出力を捕捉し、当該検査の違反件数が1以上またはwarn文言を
含む場合のみ捕捉内容を再出力する。違反0件かつwarnなしの検査は無出力とする。
"""

# pylint: disable=protected-access
# 統合ランナーは各サブスクリプトのモジュールプライベート検査関数を意図的に再利用するため、
# `_`接頭辞アクセスを許容する。
from __future__ import annotations

import argparse
import contextlib
import io
import pathlib
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from typing import Literal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
import check_deprecated_identifier_coverage  # noqa: E402
import check_line_ref  # noqa: E402
import check_plan_diff_gates  # noqa: E402
import check_plan_meta  # noqa: E402
import check_self_ref  # noqa: E402
import check_wc_projection  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts"))
import pretooluse  # noqa: E402

# pylint: enable=wrong-import-position

# `writing-standards`スキル配布物のCLI絶対パス。配布境界を保つためimportせずサブプロセス呼び出しする。
_CHECK_DASH_CLI = pathlib.Path(__file__).resolve().parents[2] / "writing-standards" / "scripts" / "check_dash.py"


def main() -> int:
    """統合ランナーのエントリポイント。"""
    parser = argparse.ArgumentParser(description="計画ファイル向け機械チェックの統合ランナー。")
    parser.add_argument("plan_paths", nargs="+", type=pathlib.Path, help="検査対象の計画ファイル（複数指定可）")
    parser.add_argument(
        "--target-repo",
        type=pathlib.Path,
        default=None,
        help="対象リポジトリのrootを明示指定する（未指定時はcwd起点で`.git`祖先を探索する）",
    )
    args = parser.parse_args()

    if args.target_repo is not None:
        repo_root = args.target_repo.expanduser().resolve()
    else:
        repo_root = check_deprecated_identifier_coverage._find_repo_root(pathlib.Path.cwd())
    total_violations = 0
    for plan_path in args.plan_paths:
        total_violations += _check_one(plan_path, repo_root)
    return 1 if total_violations > 0 else 0


def _check_one(plan_path: pathlib.Path, repo_root: pathlib.Path) -> int:
    """1計画ファイルへ全検査を実行し、違反件数を返す。"""
    violations = 0
    violations += _capture_and_relay(lambda: check_wc_projection._check_wc(plan_path))

    prose_paths: list[pathlib.Path] = []
    location_map: dict[str, str] = {}

    def _extract() -> int:
        messages, (extracted_prose, extracted_map) = check_plan_diff_gates._extract_diff_blocks(plan_path)
        prose_paths.extend(extracted_prose)
        location_map.update(extracted_map)
        return len(messages)

    violations += _capture_and_relay(_extract)
    # textlint違反は体裁系のため警告出力のみとしviolationsへは加算しない
    # （構造系の縮退フレーズ検査は`_extract`側で既に`violations`へ加算済み）。
    for msg in check_plan_diff_gates._check_extracted_paths((prose_paths, location_map)):
        print(msg, file=sys.stderr)

    violations += _capture_and_relay(lambda: check_deprecated_identifier_coverage._check_plan(plan_path, repo_root))

    text = plan_path.read_text(encoding="utf-8")
    for msg in check_line_ref._check_file(plan_path, text):
        print(msg, file=sys.stderr)
        violations += 1
    for msg in check_line_ref._check_content_level_violations(plan_path, text, repo_root):
        print(msg, file=sys.stderr)
        violations += 1
    for msg in check_self_ref._check_file(plan_path, text):
        print(msg, file=sys.stderr)
        violations += 1
    for msg in check_plan_meta._check_file(plan_path, text):
        print(msg, file=sys.stderr)
        violations += 1
    # warn出力のみでexit codeへ含めない（`check_plan_diff_gates._check_plan_file`と同じ扱い）。
    for msg in check_plan_diff_gates._check_transcription_declaration_consistency(plan_path, text, repo_root):
        print(msg, file=sys.stderr)

    _check_document_size_upper_limit(plan_path, text)
    violations += _check_version_bump_matrix(plan_path, text)
    _check_version_bump_matrix_consistency(plan_path, text)
    for msg in _check_run_method_script_paths(plan_path, text, repo_root):
        print(msg, file=sys.stderr)
        violations += 1
    for msg in _check_test_file_pairing(plan_path, text, repo_root):
        print(msg, file=sys.stderr)
    violations += _check_frontmatter_sync_note_coverage(plan_path, text, repo_root)
    violations += _check_reduction_block_text_fence(plan_path, text)

    _run_subprocess_check([sys.executable, str(_CHECK_DASH_CLI), str(plan_path)], "check_dash", blocking=False)
    return violations


def _capture_and_relay(check: Callable[[], int]) -> int:
    """検査呼び出しをstderr捕捉し、違反ありまたはwarn文言を含む場合のみ再出力する。"""
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        violations = check()
    captured = buffer.getvalue()
    if violations > 0 or "[warn]" in captured:
        sys.stderr.write(captured)
    return violations


def _run_subprocess_check(cmd: list[str], label: str, *, blocking: bool = True) -> int:
    """サブプロセスを実行し、非0終了時にstderrへ要約表示する。

    `blocking=True`の場合のみ違反ありを1として返す。`blocking=False`（体裁系チェック用）は
    警告として出力するのみでexit code集計へは常に0を返す。
    """
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return 0
    combined = (result.stdout or "") + (result.stderr or "")
    label_suffix = label if blocking else f"{label}（警告・非ブロック）"
    print(f"[{label_suffix}]\n{combined.strip()}", file=sys.stderr)
    return 1 if blocking else 0


# `### 対象ファイルの現状`直下のバレット項目からファイルパスと現行行数を抽出する
# （`- \`path\`: 現行N行`形式。対象ファイル一覧のチェックボックス確定前の調査段階記述）。
_CURRENT_LINES_BULLET_RE = re.compile(r"^-\s*`(?P<path>[^`]+)`\s*[:：]\s*現行(?P<current>\d+)行")

# `#### 縮減対象`H4見出し（見出し行）。ファイル名注記の有無を問わず一致する。
_REDUCTION_HEADING_RE = re.compile(r"^####\s*縮減対象")

# `agent-toolkit/`配下の`.md`ファイル判定用パターン。
_AGENT_TOOLKIT_MD_RE = re.compile(r"agent-toolkit/.+\.md$")

# 版更新マトリクスの列見出し（`ファイル・改訂節数・節名・判定・該当基準`の5列を持つテーブル行）。
# 定義は`agent-toolkit/scripts/_plan_format.py`のSSOTへ移設済みで、本ファイルは
# `pretooluse._plan_format._BUMP_MATRIX_HEADER_RE`を参照する（重複回避のため）。
# `scripts/agent_toolkit_bump.py`実行ステップの検出（`## 実行方法`本文中）。
_BUMP_SCRIPT_RE = re.compile(r"agent_toolkit_bump\.py")

# 縮減計画トリガー行数（`agent-toolkit:agent-standards`「文書サイズ上限」節が定める200行。
# 現行行数がこの値を超える場合、計画へ縮減対応の明示を要求する）。
_REDUCTION_PLAN_TRIGGER_LINES = 200

# `## 実行方法`節の区間抽出、およびバッククォート囲みコマンド中の拡張子付きスクリプトパス抽出用。
_RUN_METHOD_SECTION_RE = re.compile(r"^## 実行方法\s*$", re.MULTILINE)
_NEXT_H2_RE = re.compile(r"^## ", re.MULTILINE)
_BACKTICK_INLINE_RE = re.compile(r"`([^`\n]+)`")
_SCRIPT_EXT_RE = re.compile(r"\.(?:py|sh|ps1|js|ts)$")

# `.py`実装ファイルと対応する`_test.py`ペアの照合用。
_TEST_FILE_SUFFIX = "_test.py"
_TEST_PAIRING_EXCLUDES = frozenset({"__init__.py", "_test_helpers.py"})


def _collect_current_bullet_files(text: str) -> list[tuple[str, int]]:
    r"""`### 対象ファイルの現状`バレット記述`- \`path\`: 現行N行`から(相対パス, 現行行数)一覧を抽出する。

    対象ファイル一覧のチェックボックス確定前の調査段階記述であり、現行行数のみを
    縮減計画トリガー判定の入力として扱う（`_check_document_size_upper_limit`が利用する）。
    """
    return [
        (m.group("path"), int(m.group("current"))) for line in text.splitlines() if (m := _CURRENT_LINES_BULLET_RE.match(line))
    ]


def _extract_target_file_paths(text: str) -> list[str]:
    """`## 変更内容 > ### 対象ファイル一覧`のチェックボックス項目からパス一覧を抽出する。"""
    lines = text.splitlines()
    in_changes = False
    in_list_section = False
    paths: list[str] = []
    for line in lines:
        if line.startswith("## "):
            in_changes = line.strip() == "## 変更内容"
            in_list_section = False
            continue
        if not in_changes:
            continue
        if line.startswith("### "):
            in_list_section = line.strip() == "### 対象ファイル一覧"
            continue
        if not in_list_section:
            continue
        m = re.match(r"^-\s*\[[ xX]\]\s*`?(?P<path>[^`\s（(]+)`?", line)
        if m:
            paths.append(m.group("path"))
    return paths


def _check_document_size_upper_limit(plan_path: pathlib.Path, text: str) -> None:
    """縮減計画トリガー（200行）を超える`.md`対象ファイルに対し縮減対象H4または追記量圧縮の明示があるかを検査する。

    `### 対象ファイル一覧`チェックボックスの現行行数（`check_wc_projection._collect_current_line_counts`）を
    `_REDUCTION_PLAN_TRIGGER_LINES`と比較し、超過する場合を対象とする。
    対象ファイルに`#### 縮減対象`H4見出しまたは「追記量圧縮」の記述が計画本文に1件もない場合に
    warningとして報告する。体裁・構成寄りの指摘のためexit codeへは算入しない
    （判定根拠は`agent-toolkit:agent-standards`配下`references/check-script-design.md`
    「検査項目のerror・warning区分」節を参照する）。
    `### 対象ファイルの現状`バレット記述（チェックボックス確定前の調査段階記述）も同様に
    現行行数のみで超過判定する。
    """
    section = check_wc_projection._extract_section(text, "## 変更内容")
    current_map = check_wc_projection._collect_current_line_counts(section) if section is not None else {}

    over_files: list[tuple[str, int]] = []
    for path, current in current_map.items():
        if not (path.endswith(".md") or path.endswith(".md.tmpl")):
            continue
        if current > _REDUCTION_PLAN_TRIGGER_LINES:
            over_files.append((path, current))

    for path, current in _collect_current_bullet_files(text):
        if path in current_map:
            continue
        if current > _REDUCTION_PLAN_TRIGGER_LINES and (path.endswith(".md") or path.endswith(".md.tmpl")):
            over_files.append((path, current))

    if not over_files:
        return
    has_reduction_heading = any(_REDUCTION_HEADING_RE.match(line) for line in text.splitlines())
    has_reduction_note = "追記量圧縮" in text or "追記量の圧縮" in text
    if has_reduction_heading or has_reduction_note:
        return
    for path, current in over_files:
        print(
            f"[warn] {plan_path}: {path} 現行{current}行が縮減計画トリガー（{_REDUCTION_PLAN_TRIGGER_LINES}行）を超過するが、"
            f"`#### 縮減対象`H4見出しも追記量圧縮の記述も本文に存在しない",
            file=sys.stderr,
        )


def _check_reduction_block_text_fence(plan_path: pathlib.Path, text: str) -> int:
    """`#### 縮減対象`H4配下に削除文言案の`text`フェンスが存在するかを検査する。

    削除文言案のtextフェンス欠落は実装段階まで対応内容の確定を先送りする記述と同義の
    自立性違反であり、計画ファイル段階で検出する。
    `_REDUCTION_HEADING_RE`は`re.MULTILINE`フラグを持たないため、
    `finditer(text)`ではなく行単位の`match`で走査する。
    削除文言案の例示コードブロック内に見出し様の行（`#### `等）が含まれる場合、
    フェンス開閉状態を無視すると本文終端誤判定・H4見出しの誤検出の双方を招くため、
    ファイル全体を走査してフェンス外の行にのみ`_REDUCTION_HEADING_RE`・次見出し判定を適用する。
    """
    violations = 0
    lines = text.splitlines()
    in_fence = False
    fence_free_indices: list[int] = []
    for i, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            fence_free_indices.append(i)
    fence_free_set = frozenset(fence_free_indices)
    for i in fence_free_indices:
        if not _REDUCTION_HEADING_RE.match(lines[i]):
            continue
        section_lines: list[str] = []
        for j, next_line in enumerate(lines[i + 1 :], start=i + 1):
            if j in fence_free_set and re.match(r"^#{2,4} ", next_line):
                break
            section_lines.append(next_line)
        section_body = "\n".join(section_lines)
        if "```text" not in section_body:
            print(
                f"{plan_path}: `#### 縮減対象`H4節配下に削除文言案の`text`コードブロックが存在しない（自立性違反）",
                file=sys.stderr,
            )
            violations += 1
    return violations


def _check_version_bump_matrix(plan_path: pathlib.Path, text: str) -> int:
    """`agent-toolkit/`配下の`.md`が対象に含まれる場合、版更新マトリクスかbump script記載を要求する。

    `## 変更内容 > ### 対象ファイル一覧`に`agent-toolkit/`配下の`.md`ファイルが1件以上含まれる場合、
    計画本文に`ファイル・改訂節数・節名・判定・該当基準`の5列テーブルまたは
    `scripts/agent_toolkit_bump.py`の実行記載のいずれかが存在するかを検査する。
    いずれも存在しない場合に違反として報告する。exit codeへ算入する。
    版更新マトリクスの「判定」列全行が`bump不要`と確定している場合
    （`pretooluse._plan_format._all_bump_matrix_judgments_are_none_required`）は、
    以降のOR条件判定をスキップして違反なしと判定する。
    """
    target_paths = _extract_target_file_paths(text)
    if not any(_AGENT_TOOLKIT_MD_RE.search(p) for p in target_paths):
        return 0
    if pretooluse._plan_format._all_bump_matrix_judgments_are_none_required(text):
        return 0
    has_matrix = bool(pretooluse._plan_format._BUMP_MATRIX_HEADER_RE.search(text))
    has_bump_script = bool(_BUMP_SCRIPT_RE.search(text))
    if has_matrix or has_bump_script:
        return 0
    print(
        f"{plan_path}: `agent-toolkit/`配下の`.md`ファイルを対象に含むが、"
        f"版更新マトリクス（`ファイル・改訂節数・節名・判定・該当基準`5列表）も"
        f"`scripts/agent_toolkit_bump.py`実行記載も本文に存在しない",
        file=sys.stderr,
    )
    return 1


# 版更新マトリクスの判定列ラベルとbump種別ランクの対応（FB[4]）。
# `.claude/skills/agent-toolkit-edit/SKILL.md`「バージョン更新」節「判定基準」の
# MAJORがMINORより大きく、MINORがPATCHより大きく、PATCHがbump不要より大きい順と対応させる。
_BUMP_JUDGMENT_RANK: dict[str, int] = {"bump不要": 0, "PATCH": 1, "PATCH寄与": 1, "MINOR": 2, "MAJOR": 3}
_BUMP_SCRIPT_ARG_RANK: dict[str, int] = {"patch": 1, "minor": 2, "major": 3}
_BUMP_SCRIPT_ARG_RE = re.compile(r"agent_toolkit_bump\.py\s+(major|minor|patch)", re.IGNORECASE)


def _check_version_bump_matrix_consistency(plan_path: pathlib.Path, text: str) -> None:
    """版更新マトリクスの行単位判定・全体bump最大値の整合を機械照合し不整合時にwarn出力する（FB[4]）。

    改訂節数列が`1`かつ該当基準列に「新設」を含まない行で判定列が`MINOR`・`MAJOR`の場合、
    単一節追記に対する過大判定として警告する
    （`.claude/skills/agent-toolkit-edit/SKILL.md`「複数ファイルへそれぞれ単一節分の追記をする変更は、
    各ファイル単位でPATCH判定の対象とする」規定に基づく）。
    該当基準列に「新設」を含む行（節新設による`MINOR`・`MAJOR`判定）は正当な判定のため警告対象から除外する。
    `## 実行方法`H2セクション本文（コードフェンス除外）中のbump script引数（major/minor/patch）が
    マトリクス全行の判定列最大値（MAJORがMINORより大きく、MINORがPATCHより大きく、
    PATCHがbump不要より大きい順）と一致するかも警告対象とする。
    引数抽出を`## 実行方法`セクション本文に限定するのは、計画本文中の他箇所（変更案コードブロック等）に
    出現するbump引数文字列の誤認を避けるため。
    いずれも警告のみでexit codeへ算入しない（`_check_version_bump_matrix`の必須判定とは独立させる）。
    """
    rows = list(pretooluse._plan_format._BUMP_MATRIX_ROW_RE.finditer(text))
    if not rows:
        return
    for m in rows:
        revision_count = m.group("revision_count").strip()
        judgment = m.group("judgment").strip()
        criteria = m.group("criteria").strip()
        if revision_count == "1" and judgment in ("MINOR", "MAJOR") and "新設" not in criteria:
            print(
                f"{plan_path}: [warn] 版更新マトリクスの`{m.group('file')}`行は改訂節数1だが判定が`{judgment}`"
                "（単一節追記はPATCH相当が既定。過大判定の可能性）",
                file=sys.stderr,
            )
    ranks = [_BUMP_JUDGMENT_RANK.get(m.group("judgment").strip()) for m in rows]
    ranks = [r for r in ranks if r is not None]
    if not ranks:
        return
    expected_max = max(ranks)
    execution_body = "\n".join(line for _, line in pretooluse._plan_format.extract_h2_section_body(text, "実行方法"))
    script_match = _BUMP_SCRIPT_ARG_RE.search(execution_body)
    if script_match is None:
        return
    actual_rank = _BUMP_SCRIPT_ARG_RANK[script_match.group(1).lower()]
    if actual_rank != expected_max:
        print(
            f"{plan_path}: [warn] `## 実行方法`のbump種別と版更新マトリクス判定列の最大値が不一致",
            file=sys.stderr,
        )


def _check_run_method_script_paths(plan_path: pathlib.Path, text: str, repo_root: pathlib.Path) -> list[str]:
    """`## 実行方法`節内のバッククォート囲みコマンドから拡張子付きスクリプトパスを抽出し実在確認する。

    拡張子付き（`.py`・`.sh`・`.ps1`・`.js`・`.ts`）トークン限定で検査するため、
    ハイフン始まりのフラグ・`=`を含むkey=value形式のトークンは誤検出抑制のため除外する。
    """
    issues: list[str] = []
    m = _RUN_METHOD_SECTION_RE.search(text)
    if not m:
        return issues
    section_start = m.end()
    next_m = _NEXT_H2_RE.search(text, section_start)
    section_end = next_m.start() if next_m else len(text)
    section = text[section_start:section_end]
    for inline_m in _BACKTICK_INLINE_RE.finditer(section):
        raw = inline_m.group(1).strip()
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        for token in tokens:
            if not _SCRIPT_EXT_RE.search(token):
                continue
            if token.startswith("-") or "=" in token:
                continue
            if not (repo_root / token).exists():
                issues.append(f"{plan_path}: `## 実行方法`が参照するスクリプトパスが不在: {token}")
    return issues


def _check_test_file_pairing(plan_path: pathlib.Path, text: str, repo_root: pathlib.Path) -> list[str]:
    """`.py`実装ファイルに対応する`<basename>_test.py`の対象ファイル一覧欠落を検査する。

    対象ファイル一覧に`.py`実装ファイルが含まれ、リポジトリに対応する`<basename>_test.py`が
    実在する場合、テストファイルが対象ファイル一覧から欠落していないかを確認する。
    欠落検知時にwarn文言を返す。exit codeへは算入しない。
    除外対象: 末尾`_test.py`のファイル自身、`__init__.py`、`_test_helpers.py`。
    """
    warnings: list[str] = []
    target_paths = _extract_target_file_paths(text)
    target_set = set(target_paths)
    for path in target_paths:
        if not path.endswith(".py"):
            continue
        if path.endswith(_TEST_FILE_SUFFIX):
            continue
        basename = pathlib.PurePosixPath(path).name
        if basename in _TEST_PAIRING_EXCLUDES:
            continue
        test_path = path[: -len(".py")] + _TEST_FILE_SUFFIX
        if not (repo_root / test_path).exists():
            continue
        if test_path in target_set:
            continue
        warnings.append(
            f"{plan_path}: [warn] 対象ファイル{basename}に対応するテスト"
            f"{pathlib.PurePosixPath(test_path).name}が対象ファイル一覧に不在"
        )
    return warnings


def _acknowledgement_scope_text(text: str, *, exclude_line_numbers: frozenset[int] = frozenset()) -> str:
    """`## 変更内容`本文と`### エージェント判断`本文を連結し、追記漏れ判定の照合対象を限定する。

    `## 背景`配下の原文転記領域（ユーザー発話・提示素材）を含む計画全域を照合対象とすると、
    追記漏れとは無関係な文脈での偶然の文字列一致を追記漏れ判定の充足条件として誤って許容し得るため、
    判断根拠が実際に記述される`## 変更内容`（対象ファイル一覧・追記記述）と
    `### エージェント判断`（採否判断・却下理由）の2箇所へ限定する。
    `exclude_line_numbers`に含まれる行は`## 変更内容`本文から除外する
    （対象ファイル自身のH3本文を部分文字列除去ではなく行番号で除いた充足判定スコープを得るために用いる）。
    """
    changes_body = "\n".join(
        line
        for line_num, line in pretooluse._plan_format.extract_h2_section_body(text, "変更内容")
        if line_num not in exclude_line_numbers
    )
    judgment_lines: list[str] = []
    for heading, body in pretooluse._plan_format.iter_h3_sections_under_h2(text, "対応方針"):
        if heading == "エージェント判断":
            judgment_lines = [line for _, line in body]
            break
    return changes_body + "\n" + "\n".join(judgment_lines)


def _target_file_h3_line_numbers(text: str, path: str) -> frozenset[int]:
    """対象ファイル`path`に対応する`## 変更内容`配下H3配下の行番号集合を返す。

    バッククォート囲みの`path`と一致するH3見出し、または`change-content-supplement.md`
    規定の集約H3（`置換パターン:`prefix）配下でsublistに`path`を含むH3を対象H3として扱う。
    対応するH3が存在しない場合は空集合を返す。
    """
    line_numbers: set[int] = set()
    for heading, body in pretooluse._plan_format.iter_h3_sections_under_h2(text, "変更内容"):
        heading_stripped = heading.strip().strip("`")
        body_text = "\n".join(line for _, line in body)
        if heading_stripped == path or (heading.startswith("置換パターン:") and f"`{path}`" in body_text):
            line_numbers.update(line_num for line_num, _ in body)
    return frozenset(line_numbers)


def _target_file_h3_scope_text(text: str, line_numbers: frozenset[int]) -> str:
    """`line_numbers`が指す`## 変更内容`配下の行を本文順に連結して返す。

    「対象ファイル固有スコープに参照が言及されるか」という追加照合の限定判定に用いる。
    """
    return "\n".join(
        line for line_num, line in pretooluse._plan_format.extract_h2_section_body(text, "変更内容") if line_num in line_numbers
    )


def _sync_note_reference_verdict(needles: tuple[str, ...], h3_scope_text: str) -> Literal["error", "warning"]:
    """充足しなかった同期注記参照1件を`error`／`warning`のいずれで扱うかを判定する。

    `h3_scope_text`が空（対象H3が計画本文に存在しない）場合は、`plan-file-guidelines.md`
    「変更内容（`## 変更内容`）」節が規定するH3見出しラベルとファイルパスの1対1対応が
    将来的に遵守される前提として据え置き`error`を返す。
    `h3_scope_text`が非空で`needles`をいずれも含まない場合は無関係な既存同期注記とみなし`warning`を返す。
    `h3_scope_text`が非空で`needles`のいずれかを含む場合は計画の実際の追記/置換位置と
    概念的に関連する参照とみなし`error`を返す。
    """
    if not h3_scope_text or any(needle in h3_scope_text for needle in needles):
        return "error"
    return "warning"


def _check_frontmatter_sync_note_coverage(plan_path: pathlib.Path, text: str, repo_root: pathlib.Path) -> int:
    """対象ファイル一覧の各ファイルが冒頭に同期注記を持つ場合の追記漏れを検査する。

    対象判定・同期注記の抽出・分離・参照抽出は`pretooluse.py`のSSOT実装
    （`_is_frontmatter_sync_check_target`・`_extract_frontmatter_sync_notes`・
    `_split_sync_note_block`・`_extract_sync_note_references`）を再利用する。
    充足判定は`_acknowledgement_scope_text`が返すスコープから対象ファイル自身のH3本文を除いた
    範囲への言及（対象ファイル一覧への追加・他ファイルH3での言及・`### エージェント判断`での
    明示的な更新不要判断）で行う。充足しない場合の`error`／`warning`区分判定は
    `_sync_note_reference_verdict`へ委譲する（対象ファイル固有H3配下スコープに参照が
    現れるかどうかで、計画の実際の追記/置換位置と概念的に関連する参照のみを`error`として残す）。
    参照先ファイルは`repo_root`起点で解決し、実在しない参照は対象外として扱う。
    `read_text()`実行時の`OSError`・`UnicodeDecodeError`等の例外は捕捉し、
    stderr出力・違反件数1加算後に検査を継続する（統合ランナー全体の異常終了を防ぐ）。
    """
    target_paths = _extract_target_file_paths(text)
    target_set = set(target_paths)
    violations = 0
    for path in target_paths:
        if not pretooluse._is_frontmatter_sync_check_target(path):
            continue
        file_path = repo_root / path
        if not file_path.exists():
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"{plan_path}: {path} の読み込みに失敗: {exc}", file=sys.stderr)
            violations += 1
            continue
        h3_line_numbers = _target_file_h3_line_numbers(text, path)
        h3_scope_text = _target_file_h3_scope_text(text, h3_line_numbers)
        other_scope_text = _acknowledgement_scope_text(text, exclude_line_numbers=h3_line_numbers)
        notes = pretooluse._extract_frontmatter_sync_notes(content)
        for note in notes:
            paths, sections = pretooluse._extract_sync_note_references(note)
            for referenced in paths:
                resolved = pretooluse._resolve_referenced_path(str(file_path), referenced)
                if resolved is None:
                    continue
                try:
                    rel = resolved.resolve().relative_to(repo_root.resolve())
                except ValueError:
                    continue
                rel_str = rel.as_posix()
                if rel_str == path or rel_str in target_set or rel_str in other_scope_text or referenced in other_scope_text:
                    continue
                verdict = _sync_note_reference_verdict((rel_str, referenced), h3_scope_text)
                prefix = "" if verdict == "error" else "[warn] "
                suffix = "" if verdict == "error" else "（対象ファイル固有H3配下では言及なし）"
                print(
                    f"{plan_path}: {prefix}{path} の冒頭同期注記が参照する {rel_str} が対象ファイル一覧に不在{suffix}",
                    file=sys.stderr,
                )
                if verdict == "error":
                    violations += 1
            for section_name in sections:
                if section_name in other_scope_text:
                    continue
                verdict = _sync_note_reference_verdict((section_name,), h3_scope_text)
                prefix = "" if verdict == "error" else "[warn] "
                suffix = "" if verdict == "error" else "（対象ファイル固有H3配下では言及なし）"
                print(
                    f"{plan_path}: {prefix}{path} の冒頭同期注記が参照する節"
                    f"「{section_name}」が計画本文の追記記述に不在{suffix}",
                    file=sys.stderr,
                )
                if verdict == "error":
                    violations += 1
    return violations


if __name__ == "__main__":
    sys.exit(main())
