#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""計画ファイル向け機械チェックの統合ランナー。

`agent-toolkit:plan-mode`の書き込み後チェック（工程6-2直後）・工程7メイン側セルフチェック・
実装後検証の各契機から、1回のプロセス起動で下記検査を実行する。個別スクリプトを都度起動する運用は
サブプロセス起動段数・出力量が累積してコンテキストを汚染するため、本スクリプトへ統合する。

実行する検査:

- `check_wc_projection._check_wc`: 見込み行数と`wc -l`実測値の乖離検出
- `check_plan_diff_gates._extract_diff_blocks`・`_check_extracted_paths`: 差分ブロック抽出、
  縮退フレーズの検査（exit codeへ算入）。textlintは警告出力のみで
  exit codeへ算入しない（体裁・表記系は計画作成の往復削減方針により非ブロック化する）
- `check_plan_diff_gates._check_transcription_declaration_consistency`: 「同構造」「同旨」「同期」
  宣言表現検出時の対象ファイル本体との整合性検査（warn出力のみ、exit codeへ含めない）
- `check_plan_diff_gates._check_recurrence_prevention_recorded`: `### 恒久化・リファクタリング内容`
  小見出し配下の再発予防記述要件の照合（ユーザー指示による機械ゲート化、exit codeへ含める）
- `check_deprecated_identifier_coverage._check_plan`: `#### 廃止・改名対象一覧`存在時の残存参照照合
- `check_line_ref._check_file`・`_check_content_level_violations`: 行番号参照・パス実在・
  スキル名実在・節名実在（パス付き節名参照形式および裸参照形式）の検査
- `check_self_ref._check_file`: 自己参照曖昧候補・禁止形式候補の検査
- `check_plan_meta._check_file`: `## 背景`配下`### 計画メタ情報`H3と起動経路・対象リポジトリ2項目の欠落検査
- `writing-standards/scripts/check_dash.py`: 和文ハイフン検査（サブプロセス、ファイル単位）。
  警告出力のみでexit codeへ算入しない
- `_check_document_size_upper_limit`: `## 変更内容`対象ファイル一覧の宣言済み見込み行数、
  および追記/縮減対象集計からの計算見込み行数のいずれかが220行超過する`.md`ファイルについて、
  対応する`#### 縮減対象`H4見出しまたは追記量圧縮の記述が計画本文に存在するかを検査する
  （体裁・構成寄りの指摘のためwarning出力のみとしexit codeへ算入しない）
- `_check_version_bump_matrix`: `## 変更内容`対象ファイル一覧が`agent-toolkit/`配下の`.md`ファイルを
  含む計画で、`ファイル・改訂節数・節名・判定・該当基準`の5列表または`scripts/agent_toolkit_bump.py`の
  種別記載のいずれかが計画本文に存在するかを検査する（不在時に違反として報告、exit codeへ算入）
- `_check_run_method_script_paths`: `## 実行方法`節内のバッククォート囲みコマンドから
  拡張子付き（`.py`・`.sh`・`.ps1`・`.js`・`.ts`）スクリプトパスを抽出し、
  プロジェクトルート起点で実在するかを検査する（不在時に違反として報告、exit codeへ算入）
- `_check_test_file_pairing`: 対象ファイル一覧の`.py`実装ファイルに対応する`<basename>_test.py`が
  リポジトリに実在するのに対象ファイル一覧から欠落していないかを検査する（warn出力のみ、exit code非算入）。

体裁・表記系（和文ハイフン）と、文書サイズ上限検査（`_check_document_size_upper_limit`）は
全て警告出力のみとしexit codeへ算入しない。textlint・markdownlint・typos・口語表現の全文検査は
工程7ステップ4の`uvx pyfltr run-for-agent`実行（本ランナー外）でのみ行う。
成功時（構造系0違反）はexit 0で終了する。体裁系のみ違反時もexit 0で終了し、
警告として`stderr`へ出力する。構造系の違反検出時は検査名ごとに要点を`stderr`へ集約してexit 1で
終了する。`uvx pyfltr`のJSONL出力はヘッダ行・succeeded系サマリー行を含み冗長なため生出力を
転記せず、diagnostics保有行と失敗系command行のみ`file:line: message`形式または
`[pyfltr] <command>: <status> <message>`形式へ要約する
（出力ノイズ削減が本統合ランナー導入の主目的の一つ）。

import再利用する下位検査関数（`check_wc_projection._check_wc`・
`check_plan_diff_gates._extract_diff_blocks`・`check_deprecated_identifier_coverage._check_plan`）は
違反0件でもstderrへ情報出力・warn出力する副作用を持つ。本ランナーは検査単位で
`contextlib.redirect_stderr`により出力を捕捉し、当該検査の違反件数が1以上またはwarn文言を
含む場合のみ捕捉内容を再出力する。違反0件かつwarnなしの検査は無出力とする。

設計原則は`agent-toolkit:agent-standards`配下
`references/check-script-design.md`を参照する。
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
import check_deprecated_identifier_coverage  # noqa: E402
import check_line_ref  # noqa: E402
import check_plan_diff_gates  # noqa: E402
import check_plan_meta  # noqa: E402
import check_self_ref  # noqa: E402
import check_wc_projection  # noqa: E402

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
    for msg in _check_run_method_script_paths(plan_path, text, repo_root):
        print(msg, file=sys.stderr)
        violations += 1
    for msg in _check_test_file_pairing(plan_path, text, repo_root):
        print(msg, file=sys.stderr)

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
_BUMP_MATRIX_HEADER_RE = re.compile(r"\|\s*ファイル\s*\|\s*改訂節数\s*\|\s*節名\s*\|\s*判定\s*\|\s*該当基準\s*\|")
# `scripts/agent_toolkit_bump.py`実行ステップの検出（`## 実行方法`本文中）。
_BUMP_SCRIPT_RE = re.compile(r"agent_toolkit_bump\.py")

# 文書サイズ上限（`agent-toolkit:agent-standards`「文書サイズ上限」節が定める220行）。
_DOCUMENT_SIZE_UPPER_LIMIT = 220

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

    対象ファイル一覧のチェックボックス確定前の調査段階記述であり、見込み値は持たないため
    現行行数のみを220行超過判定の入力として扱う（`_check_document_size_upper_limit`が利用する）。
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
    """220行超過の`.md`対象ファイルに対し縮減対象H4または追記量圧縮の明示があるかを検査する。

    宣言済み見込み行数（`### 対象ファイル一覧`チェックボックスの`見込みM行`）と、
    `check_wc_projection.extract_addition_reduction_blocks`による追記/縮減対象集計から算出する
    計算見込み（`宣言済み現行行数 + 追記行数 - 縮減対象行数`）の両方を`_DOCUMENT_SIZE_UPPER_LIMIT`と比較し、
    いずれか一方でも超過する場合を対象とする。計画執筆時点で対比ブロックへ書ききれていない超過を
    宣言値だけの照合では見逃すため、実績集計からの計算見込みも併用する。
    対象ファイルに`#### 縮減対象`H4見出しまたは「追記量圧縮」の記述が計画本文に1件もない場合に
    warningとして報告する。体裁・構成寄りの指摘のためexit codeへは算入しない
    （判定根拠は`agent-toolkit:agent-standards`配下`references/check-script-design.md`
    「検査項目のerror・warning区分」節を参照する）。
    `### 対象ファイルの現状`バレット記述（チェックボックス確定前の調査段階記述）は宣言済み見込み値を
    持たないため、従来どおり現行行数のみで超過判定する。
    """
    section = check_wc_projection._extract_section(text, "## 変更内容")
    bounds = check_wc_projection._collect_projection_bounds(section) if section is not None else {}
    counted_map = check_wc_projection.extract_addition_reduction_blocks(section) if section is not None else {}

    over_files: list[tuple[str, int]] = []
    for path, (current, projected) in bounds.items():
        if not (path.endswith(".md") or path.endswith(".md.tmpl")):
            continue
        counted = counted_map.get(path, {})
        computed = check_wc_projection.compute_expected_line_count(current, counted)
        limit_value = max(projected, computed)
        if limit_value > _DOCUMENT_SIZE_UPPER_LIMIT:
            over_files.append((path, limit_value))

    for path, current in _collect_current_bullet_files(text):
        if path in bounds:
            continue
        if current > _DOCUMENT_SIZE_UPPER_LIMIT and (path.endswith(".md") or path.endswith(".md.tmpl")):
            over_files.append((path, current))

    if not over_files:
        return
    has_reduction_heading = any(_REDUCTION_HEADING_RE.match(line) for line in text.splitlines())
    has_reduction_note = "追記量圧縮" in text or "追記量の圧縮" in text
    if has_reduction_heading or has_reduction_note:
        return
    for path, projected in over_files:
        print(
            f"[warn] {plan_path}: {path} 見込み{projected}行が文書サイズ上限（{_DOCUMENT_SIZE_UPPER_LIMIT}行）を超過するが、"
            f"`#### 縮減対象`H4見出しも追記量圧縮の記述も本文に存在しない",
            file=sys.stderr,
        )


def _check_version_bump_matrix(plan_path: pathlib.Path, text: str) -> int:
    """`agent-toolkit/`配下の`.md`が対象に含まれる場合、版更新マトリクスかbump script記載を要求する。

    `## 変更内容 > ### 対象ファイル一覧`に`agent-toolkit/`配下の`.md`ファイルが1件以上含まれる場合、
    計画本文に`ファイル・改訂節数・節名・判定・該当基準`の5列テーブルまたは
    `scripts/agent_toolkit_bump.py`の実行記載のいずれかが存在するかを検査する。
    いずれも存在しない場合に違反として報告する。exit codeへ算入する。
    """
    target_paths = _extract_target_file_paths(text)
    if not any(_AGENT_TOOLKIT_MD_RE.search(p) for p in target_paths):
        return 0
    has_matrix = bool(_BUMP_MATRIX_HEADER_RE.search(text))
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


if __name__ == "__main__":
    sys.exit(main())
