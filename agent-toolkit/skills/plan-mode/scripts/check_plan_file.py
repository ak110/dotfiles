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
  縮退フレーズ・textlint・line-width（127幅）の検査
- `check_deprecated_identifier_coverage._check_plan`: `#### 廃止・改名対象一覧`存在時の残存参照照合
- `check_line_ref._check_file`・`_check_content_level_violations`: 行番号参照・パス実在・
  スキル名実在・件数表現の検査
- `check_self_ref._check_file`: 自己参照曖昧候補・禁止形式候補の検査
- `writing-standards/scripts/check_line_width.py`: 127幅検査（サブプロセス、ファイル単位）
- `writing-standards/scripts/check_dash.py`: 和文ハイフン検査（サブプロセス、ファイル単位）
- `uvx pyfltr run-for-agent --commands=textlint,markdownlint,typos,colloquial-check
  --enable=colloquial-check`: 計画ファイル全域のtextlint・markdownlint・typos・口語表現検査
  （サブプロセス、JSONL出力を解析し`kind == "diagnostic"`のレコード、および
  `kind == "command"`かつ失敗系statusのレコードのみ要約表示）

成功時（全項目0違反）はexit 0で無出力。違反検出時は検査名ごとに要点を`stderr`へ集約してexit 1で
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
import json
import pathlib
import subprocess
import sys
from collections.abc import Callable

_FAILED_COMMAND_STATUSES = frozenset({"failed", "resolution_failed"})

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# pylint: disable=wrong-import-position
import check_deprecated_identifier_coverage  # noqa: E402
import check_line_ref  # noqa: E402
import check_plan_diff_gates  # noqa: E402
import check_self_ref  # noqa: E402
import check_wc_projection  # noqa: E402

# pylint: enable=wrong-import-position

# `writing-standards`スキル配布物のCLI絶対パス。配布境界を保つためimportせずサブプロセス呼び出しする。
_CHECK_LINE_WIDTH_CLI = pathlib.Path(__file__).resolve().parents[2] / "writing-standards" / "scripts" / "check_line_width.py"
_CHECK_DASH_CLI = pathlib.Path(__file__).resolve().parents[2] / "writing-standards" / "scripts" / "check_dash.py"


def main() -> int:
    """統合ランナーのエントリポイント。"""
    parser = argparse.ArgumentParser(description="計画ファイル向け機械チェックの統合ランナー。")
    parser.add_argument("plan_paths", nargs="+", type=pathlib.Path, help="検査対象の計画ファイル（複数指定可）")
    args = parser.parse_args()

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
    line_width_paths: list[pathlib.Path] = []

    def _extract() -> int:
        messages, (extracted_prose, extracted_line_width) = check_plan_diff_gates._extract_diff_blocks(plan_path)
        prose_paths.extend(extracted_prose)
        line_width_paths.extend(extracted_line_width)
        return len(messages)

    violations += _capture_and_relay(_extract)
    for msg in check_plan_diff_gates._check_extracted_paths((prose_paths, line_width_paths)):
        print(msg, file=sys.stderr)
        violations += 1

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

    violations += _run_subprocess_check([sys.executable, str(_CHECK_LINE_WIDTH_CLI), str(plan_path)], "check_line_width")
    violations += _run_subprocess_check([sys.executable, str(_CHECK_DASH_CLI), str(plan_path)], "check_dash")
    violations += _run_pyfltr_jsonl(plan_path)
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


def _run_subprocess_check(cmd: list[str], label: str) -> int:
    """サブプロセスを実行し、非0終了時にstderrへ要約表示する。違反ありなら1を返す。"""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return 0
    combined = (result.stdout or "") + (result.stderr or "")
    print(f"[{label}]\n{combined.strip()}", file=sys.stderr)
    return 1


def _run_pyfltr_jsonl(plan_path: pathlib.Path) -> int:
    """`uvx pyfltr run-for-agent`をJSONL出力で実行し、diagnosticsレコードと失敗系command行を要約表示する。"""
    result = subprocess.run(
        [
            "uvx",
            "pyfltr",
            "run-for-agent",
            "--commands=textlint,markdownlint,typos,colloquial-check",
            "--enable=colloquial-check",
            "--exclude-fence-under=## 背景",
            str(plan_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    violations = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "diagnostic":
            path = record.get("path", plan_path)
            lineno = record.get("line", "?")
            message = record.get("message", "")
            print(f"[pyfltr] {path}:{lineno}: {message}", file=sys.stderr)
            violations += 1
        elif record.get("kind") == "command" and record.get("status") in _FAILED_COMMAND_STATUSES:
            command = record.get("command", "?")
            status = record.get("status", "?")
            message = record.get("message", "")
            print(f"[pyfltr] {command}: {status} {message}", file=sys.stderr)
            violations += 1
    return violations


if __name__ == "__main__":
    sys.exit(main())
