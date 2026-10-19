"""保存した試験結果と実行版を読み、担当が選んだ要求行へ根拠を関連付ける。"""

from __future__ import annotations

import argparse
import collections
import collections.abc
import copy
import json
import os
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any

import accepted_review_evidence
import verification_diagnostics
from verification_diagnostics import path_value, run_record

from agent_toolkit._common import next_action, process_tree
from agent_toolkit._common.atomic_file import atomic_write

SCHEMA = (
    '試験JSON: {"tests":{"format":"vitest|playwright|trx","path":"/絶対/結果","run_record":"/絶対/record.json"},'
    '"baseline_tests":{同形式}}。直接指定は--test-format・--test-file・--test-record、'
    "基準版は--baseline-test-format・--baseline-test-file・--baseline-test-record。"
    "vitestは--reporter=json --outputFile=/絶対/結果、Playwrightはenv PLAYWRIGHT_JSON_OUTPUT_NAME=/絶対/結果 "
    'playwright test --reporter=json、dotnet testは--logger "trx;LogFileName=/絶対/結果"でatk run-commandから取得する。'
    '結果JSON: {"junit":{"xml":"/絶対/XML","run_record":"/絶対/record.json"},'
    '"baseline_junit":{同形式},"diagnostics":{"path":"/絶対/JSONL","run_record":"/絶対/record.json",'
    '"head":"完全OID","conditions":{"scope":[],"options":[],"dependencies":{},"parallelism":1,"environment":{}},'
    '"sources":{"worktreeのルートからの相対ファイル":"/絶対/保存時本文"}},"baseline_diagnostics":{同形式}}。'
    '不要な組は省略できる。更新JSONは配列: [{"section":"wi_conditions","row":1,"source":"一覧の完全一致値",'
    '"tests":["相対file::Class::test[param]"],"diagnostics":["added:1"],'
    '"evidence_file":"/絶対/説明.txt","mode":"append"}]。'
    "sourceは現行行との完全一致による選択値。レビュー更新のnew_sourceは保存する新しい所在で、"
    "wi_conditionsの失効、user_requirementsの失効・割当外・背景だけで指定できる。"
    "説明ファイルだけの更新はtests・diagnosticsと結果指定を省略できる。"
    "レビューはoutcomeとreviewed_headも各行で明示し、verification_sourceに未判定記録の出所を指定できる。判定は結果から自動生成しない。"
    'レビュー更新だけはaccepted_source:{"path":"/絶対/受理済み.json","section":"wi_conditions","row":1}も受ける。'
    "同じ要求単位の判定済み行への参照とevidence_fileの説明だけをmode replaceで保存し、未判定継承・結果取込みとは分ける。"
    "選択結果の根拠は保存物の同じ組の参照をまとめ、各テスト名・状態・case、診断の差の種類・entry・message_positionを全件残す。"
    "--evidence-file・--result-test・--result-diagnosticは全選択行で共通に使う値。"
    "行別には--row-evidence-file SECTION:N PATH・--row-result-test SECTION:N NAME・"
    "--row-result-diagnostic SECTION:N IDを反復する。"
    "--select-rowを省略した更新は、行別指定（レビュー更新では--row-outcome・--row-source・"
    "--verification-rowと--accepted-rowの第1要素を含む）の更新先の行を重複なく選ぶ。"
    "共通値だけの更新は--select-rowで行を選ぶ。--select-rowを明示した場合は、その集合外の行別指定を拒否する。"
    "同じ種類の共通指定と行別指定は別々に使う。単一値オプションの異なる値での反復は拒否する。"
    "モードや取得版が異なる更新は呼び出しを分割し、行別の詳細な入力は--updates-fileでも渡せる。"
)


class _SingleValue(argparse.Action):
    """同じ単一値オプションが異なる入力を失う前に拒否する。"""

    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: Any, option_string: str | None = None
    ) -> None:
        seen = getattr(namespace, "_single_values_seen", {})
        if self.dest in seen and seen[self.dest] != values:
            parser.error(
                f"単一値の入力を異なる値で反復できません: {option_string}。"
                "行別の根拠は--row-evidence-file、試験・診断は--row-result-test・--row-result-diagnosticを使う。"
                "ほかの入力は呼び出しを分割し、複数行の更新は--updates-fileでも指定できる"
            )
        seen[self.dest] = values
        namespace._single_values_seen = seen
        setattr(namespace, self.dest, values)


class ArgumentParser(argparse.ArgumentParser):
    """両証拠操作の単一値入力を同じ条件で検証するparser。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register("action", None, _SingleValue)
        self.register("action", "store", _SingleValue)


def add_arguments(parser: argparse.ArgumentParser, *, reviewing: bool = False) -> None:
    """両公開操作へ同じ入力契約を登録する。"""
    parser.epilog = SCHEMA
    parser.register("action", None, _SingleValue)
    parser.register("action", "store", _SingleValue)
    for option, value in (("evidence-file", "PATH"), ("result-test", "NAME"), ("result-diagnostic", "ID")):
        parser.add_argument(
            "--row-" + option,
            action="append",
            nargs=2,
            metavar=("SECTION:N", value),
            help="更新する行と、その行だけへ関連付ける根拠・結果の対応。反復可。"
            "--select-rowの省略時はこの行を選ぶ。同じ種類の共通指定と分けて使う",
        )
    parser.add_argument("--results-file", type=pathlib.Path, metavar="PATH", help="保存した結果の組を持つJSONの絶対パス")
    parser.add_argument("--list-results", action="store_true", help="テスト完全名・状態・版・所在と基準版との差を表示")
    parser.add_argument(
        "--results-summary", action="store_true", help="--list-resultsで状態・差・診断のまとまりと全識別子を表示"
    )
    parser.add_argument(
        "--result-test",
        action="append",
        metavar="NAME",
        help="一覧では完全テスト名の詳細を読む。更新では全選択行に共通の試験。反復可",
    )
    parser.add_argument(
        "--result-diagnostic",
        action="append",
        metavar="ID",
        help="一覧では診断識別子の詳細を読む。更新では全選択行に共通の診断。反復可",
    )
    parser.add_argument(
        "--updates-file", type=pathlib.Path, metavar="PATH", help="出所と選択した根拠を持つ更新配列JSONの絶対パス"
    )
    targets = "・".join(f"--{option.replace('_', '-')}" for option in _row_target_options(reviewing=reviewing))
    parser.add_argument(
        "--select-row",
        action="append",
        metavar="SECTION:N",
        help="根拠を保存する配列と1始まり行番号。反復可。sourceは処理が取得する。"
        f"省略すると{targets}の更新先（第1要素）を重複なく選ぶ。"
        "共通値だけの更新では必須。明示した場合は集合外の行別指定を拒否する",
    )
    for prefix in ("junit", "baseline-junit"):
        parser.add_argument(
            f"--{prefix}-xml", type=pathlib.Path, metavar="PATH", help="保存したJUnit XML。対応する-recordと指定する"
        )
        parser.add_argument(
            f"--{prefix}-record",
            type=pathlib.Path,
            metavar="PATH",
            help="JUnitを取得したrun-commandの記録。pytestの直接起動か、--output-format=jsonlのpyfltr経由の起動を受理する",
        )
    for prefix in ("test", "baseline-test"):
        parser.add_argument(f"--{prefix}-format", choices=("vitest", "playwright", "trx"), help="試験結果の生成形式")
        parser.add_argument(f"--{prefix}-file", type=pathlib.Path, metavar="PATH", help="構造化試験結果の絶対パス")
        parser.add_argument(f"--{prefix}-record", type=pathlib.Path, metavar="PATH", help="試験を取得したrun-commandの記録")
    for prefix in ("diagnostics", "baseline-diagnostics"):
        parser.add_argument(
            f"--{prefix}-file",
            type=pathlib.Path,
            metavar="PATH",
            help="保存した構造化診断。対応する-record・-conditionsと指定する",
        )
        parser.add_argument(
            f"--{prefix}-record",
            type=pathlib.Path,
            metavar="PATH",
            help="診断を取得したrun-commandの記録。取得版もこの記録から読む",
        )
        parser.add_argument(
            f"--{prefix}-conditions",
            type=pathlib.Path,
            metavar="PATH",
            help="取得時のscope・options・dependencies・parallelism・environmentを持つ保存JSON",
        )
        parser.add_argument(
            f"--{prefix}-source",
            action="append",
            nargs=2,
            metavar=("FILE", "SNAPSHOT"),
            help="worktreeのルートからの相対ファイルと保存時本文の絶対パス。反復可",
        )
    if reviewing:
        parser.add_argument(
            "--evidence-file", type=pathlib.Path, metavar="PATH", help="全選択行へ共通に保存する説明のUTF-8ファイル"
        )
        parser.add_argument("--mode", choices=("append", "replace"), help="選択行の根拠の追記か置換")
        parser.add_argument(
            "--row-outcome",
            action="append",
            nargs=2,
            metavar=("SECTION:N", "OUTCOME"),
            help="更新する各行の明示判定。全選択行へ反復指定する。--select-rowの省略時はこの行も選ぶ",
        )
        parser.add_argument("--reviewed-head", help="全選択行を判定した対象版の完全OID")
        parser.add_argument(
            "--row-source",
            action="append",
            nargs=2,
            metavar=("SECTION:N", "SOURCE"),
            help="失効・割当外・背景の判断根拠の所在へ選択行のsourceを変更する。反復可。--select-rowの省略時はこの行も選ぶ",
        )
        parser.add_argument(
            "--verification-row",
            action="append",
            nargs=2,
            metavar=("TARGET", "PENDING"),
            help="選択行SECTION:Nと未判定記録のSECTION:Nの対応。反復可。--select-rowの省略時は第1要素の行も選ぶ",
        )
        parser.add_argument(
            "--verification-record", type=pathlib.Path, metavar="PATH", help="選択できる未判定検証記録の絶対パス"
        )
        parser.add_argument(
            "--accepted-evidence",
            type=pathlib.Path,
            metavar="PATH",
            help="受理済みの完成条件証拠の絶対パス。根拠本文を複製せず判定済み行を参照する",
        )
        parser.add_argument(
            "--accepted-row",
            action="append",
            nargs=2,
            metavar=("TARGET", "ACCEPTED"),
            help="選択行SECTION:Nと同じ配列の受理済み行SECTION:Nの対応。--select-rowの省略時は第1要素の行も選ぶ。"
            "--accepted-evidence・--evidence-file（または同じ行の--row-evidence-file）・--mode replaceと指定する",
        )
        parser.add_argument(
            "--output", type=pathlib.Path, metavar="PATH", help="未判定記録と異なる完成条件証拠の絶対パス。入力証拠を更新できる"
        )


def load_json(path: pathlib.Path) -> Any:
    """UTF-8の保存JSONを読む。"""
    return json.loads(path_value(str(path)).read_text(encoding="utf-8"))


# JUnitの取得条件を読めない組へ示す、受理する起動の形と再実行の操作。
_JUNIT_RERUN = (
    "受理する起動はpytestの直接起動か、--output-format=jsonlで実行アーカイブを有効にしたpyfltr経由"
    "（run・run-for-agent・ci・fast）である。"
    "atk run-command -- ... pytest -v -o junit_family=xunit1 --junitxml=<XMLの絶対パス> <対象...>で再実行し、"
    "そのXMLと記録を指定する"
)
_PYFLTR_RUNS = {"run", "run-for-agent", "ci", "fast"}


def _junit_rejection(reason: str) -> next_action.ActionableError:
    return next_action.ActionableError(reason, next_action=_JUNIT_RERUN)


def _pyfltr_show(run_id: str, *options: str) -> dict[str, Any]:
    """pyfltrの公開操作show-runで実行記録を読む。pyfltrはagent-toolkitの依存として同じ実行環境にある。"""
    try:
        result = process_tree.run_with_timeout(
            [sys.executable, "-m", "pyfltr", "show-run", run_id, *options, "--output-format=json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _junit_rejection(f"pyfltrの実行記録{run_id}の読取を完了できません: {error}") from error
    if result.returncode:
        raise _junit_rejection(
            f"pyfltrの実行記録{run_id}を読めません（--no-archiveでの実行か保持期間の経過）: {result.stderr.strip()}"
        )
    shown = json.loads(result.stdout)
    if not isinstance(shown, dict):
        raise _junit_rejection(f"pyfltrの実行記録{run_id}の形式が不正です")
    return shown


def _junit_origin(record: dict[str, Any], record_path: pathlib.Path) -> tuple[list[str], list[tuple[pathlib.Path, str]]]:
    """XMLを書いたpytestの起動引数と、作業ディレクトリごとのrootdirを、その実行主体の記録から読む。

    直接起動ではrun-commandの記録、pyfltr経由ではpyfltrの実行記録を読む。
    pyfltr経由の記録はトップレベルのargvと標準出力がpyfltr自身のものであり、pytestの取得条件を含まないためである。
    記録のargvから組み立てたコマンドは実行しない。
    """
    argv, cwd = record["argv"], pathlib.Path(record["cwd"])
    stdout = path_value(record["stdout_path"]).read_text(encoding="utf-8")
    launcher = next((i for i, value in enumerate(argv[:-1]) if pathlib.Path(value).stem == "pyfltr"), None)
    if launcher is None or argv[launcher + 1] not in _PYFLTR_RUNS:
        return argv, [(cwd, root) for root in re.findall(r"^rootdir: (.+)$", stdout, re.MULTILINE)]
    try:
        header = json.loads(stdout.splitlines()[0]) if stdout.strip() else None
    except json.JSONDecodeError:
        header = None
    run_id = header.get("run_id") if isinstance(header, dict) and header.get("kind") == "header" else None
    if not isinstance(run_id, str) or not run_id:
        raise _junit_rejection(f"pyfltrの標準出力の先頭にJSON Linesのheaderが無く、実行記録を特定できません: {record_path}")
    meta = _pyfltr_show(run_id).get("meta")
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("cwd"), str)
        or pathlib.Path(meta["cwd"]).resolve() != cwd.resolve()
    ):
        raise _junit_rejection(f"pyfltrの実行記録{run_id}のcwdが記録cwdと一致しません: {record_path}")
    command = _pyfltr_show(run_id, "--commands", "pytest").get("command")
    commandline = command.get("commandline") if isinstance(command, dict) else None
    output = _pyfltr_show(run_id, "--commands", "pytest", "--output").get("output")
    if not isinstance(commandline, list) or any(not isinstance(v, str) for v in commandline) or not isinstance(output, str):
        raise _junit_rejection(f"pyfltrの実行記録{run_id}にpytestのcommandlineと生出力がありません")
    # サブプロジェクトごとの実行は生出力を`# subproject: <相対>`で区切り、各区間のpytestはその配下で起動する。
    roots: list[tuple[pathlib.Path, str]] = []
    section = cwd
    for line in output.splitlines():
        if header_match := re.fullmatch(r"# subproject: (.+)", line):
            section = cwd / header_match[1]
        elif root_match := re.fullmatch(r"rootdir: (.+)", line):
            roots.append((section, root_match[1]))
    return commandline, roots


def _junit(spec: object) -> dict[str, dict[str, Any]]:
    if not isinstance(spec, dict) or set(spec) != {"xml", "run_record"}:
        raise ValueError("JUnitの組にはxmlとrun_recordを指定する")
    xml = path_value(spec["xml"])
    record_path, record = run_record(spec["run_record"])
    argv, roots = _junit_origin(record, record_path)
    if len(roots) != 1 or not pathlib.Path(roots[0][1]).is_absolute():
        raise _junit_rejection(
            f"保存出力のpytest実行rootを一意に特定できません（pyfltrの複数サブプロジェクトの実行を含む）: {record_path}"
        )
    pytest_cwd, rootdir = roots[0]
    xml_args = [v.split("=", 1)[1] for v in argv if v.startswith("--junitxml=")]
    xml_args.extend(argv[i + 1] for i, v in enumerate(argv[:-1]) if v == "--junitxml")
    if len(xml_args) != 1 or (pytest_cwd / xml_args[0]).resolve() != xml.resolve():
        raise _junit_rejection(f"XMLと実行argvの--junitxmlが対応しません: {xml}, {record_path}")
    if "junit_family=xunit1" not in argv:
        raise _junit_rejection(f"file属性を保存するjunit_family=xunit1の指定が実行argvにありません: {record_path}")
    # 完全テスト名のファイル部分は、参照の所在確認と同じworktreeのルートを起点にする。
    worktree = verification_diagnostics.worktree_root(record)
    try:
        prefix = pathlib.Path(rootdir).resolve().relative_to(worktree.resolve())
    except ValueError as error:
        raise next_action.ActionableError(
            f"pytest実行root {rootdir} が対象worktree {worktree} の外です",
            next_action="--rootdirを省くか対象worktree内のディレクトリを指定してpytestを再実行し、そのXMLと記録を指定する",
        ) from error
    try:
        root = ET.parse(xml).getroot()
    except ET.ParseError as error:
        raise ValueError(f"JUnitが破損しています: {xml}: {error}") from error
    results: dict[str, dict[str, Any]] = {}
    for number, case in enumerate(root.iter("testcase"), 1):
        filename, classname, name = (case.get(key, "") for key in ("file", "classname", "name"))
        if not filename or not name or not classname:
            raise ValueError(f"JUnitのfile・classname・nameが不足しています: {xml}: testcase[{number}]")
        file_path = pathlib.Path(filename)
        if file_path.is_absolute() or ".." in file_path.parts:
            raise ValueError(f"JUnitのfileは実行rootからの相対パスが必要です: {filename}")
        module = file_path.with_suffix("").as_posix().replace("/", ".")
        if classname != module and not classname.startswith(module + "."):
            raise ValueError(f"JUnitのfileとclassnameが対応しません: {filename}, {classname}")
        suffix = classname.removeprefix(module).strip(".")
        identity = "::".join([(prefix / file_path).as_posix(), *(suffix.split(".") if suffix else []), name])
        states = [child.tag for child in case if child.tag in {"failure", "error", "skipped"}]
        if len(states) > 1 or identity in results:
            raise ValueError(f"状態かテスト識別子が一意ではありません: {xml}: {identity}")
        state = {"failure": "failed", "error": "error", "skipped": "skipped"}.get(states[0], "passed") if states else "passed"
        results[identity] = {
            "test": identity,
            "status": state,
            "xml": str(xml),
            "path": str(xml),
            "format": "pytest",
            "case": number,
            "run_record": str(record_path),
            "git_head": record["git_head"],
            "git_status": record["git_status"],
            "child_exit_code": record["child_exit_code"],
            "stdout": record["stdout_path"],
            "stderr": record["stderr_path"],
        }
    if not results or record["child_exit_code"] not in {0, 1}:
        raise ValueError(f"有効な試験の終了状態とtestcaseが必要です: {xml}")
    failed = any(item["status"] in {"failed", "error"} for item in results.values())
    if failed != (record["child_exit_code"] == 1):
        raise ValueError(f"JUnitの状態と子の終了コードが一致しません: {xml}")
    return results


def _relative_file(filename: object, root: pathlib.Path, worktree: pathlib.Path) -> str:
    if not isinstance(filename, str) or not filename:
        raise ValueError("試験のfileが不足しています")
    try:
        return (root / filename).resolve().relative_to(worktree.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"試験のfileが対象worktreeの外です: {filename}") from error


def _titles(values: object) -> list[str]:
    if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
        raise ValueError("試験のtitle列は空でない文字列の配列が必要です")
    return values


def _vitest(data: Any, cwd: pathlib.Path, worktree: pathlib.Path) -> list[tuple[str, str]]:
    cases = []
    for suite in data["testResults"]:
        filename = _relative_file(suite["name"], cwd, worktree)
        if not suite["assertionResults"] and suite.get("status") == "failed":
            raise ValueError(f"vitestの試験収集が完了していません: {filename}。収集エラーを直して再実行する")
        for case in suite["assertionResults"]:
            name = case["title"]
            if not isinstance(name, str) or not name:
                raise ValueError("vitestのtitleが不足しています")
            identity = "::".join([filename, *_titles(case["ancestorTitles"]), name])
            status = {"passed": "passed", "failed": "failed", "pending": "skipped", "skipped": "skipped", "todo": "skipped"}[
                case["status"]
            ]
            cases.append((identity, status))
    return cases


def _playwright(data: Any, cwd: pathlib.Path) -> list[tuple[str, str]]:
    root = pathlib.Path(data["config"]["rootDir"])
    if not root.is_absolute() or data.get("errors"):
        raise ValueError("Playwrightの絶対rootDirと実行エラーのない結果が必要です")
    cases: list[tuple[str, str]] = []

    def visit(suites: Any, titles: list[str]) -> None:
        for suite in suites:
            # 最外のsuite.titleはファイル名。fileから相対パスを確定する。
            ancestors = [*titles, *_titles([suite["title"]])] if titles else _titles([suite["title"]])
            for spec in suite.get("specs", []):
                filename = _relative_file(spec["file"], root, cwd)
                for case in spec["tests"]:
                    project = case["projectName"]
                    if not isinstance(project, str):
                        raise ValueError("PlaywrightのprojectNameが不足しています")
                    identity = "::".join(
                        [filename, *ancestors[1:], *_titles([spec["title"]]), json.dumps(project, ensure_ascii=False)]
                    )
                    status = {"expected": "passed", "unexpected": "failed", "flaky": "passed", "skipped": "skipped"}[
                        case["status"]
                    ]
                    cases.append((identity, status))
            visit(suite.get("suites", []), ancestors)

    visit(data["suites"], [])
    return cases


def _trx(path: pathlib.Path) -> list[tuple[str, str]]:
    root = ET.parse(path).getroot()
    if root.tag.rsplit("}", 1)[-1] != "TestRun":
        raise ValueError("TRXのTestRunが不足しています")
    return [
        (
            case.attrib["testName"],
            {"Passed": "passed", "Failed": "failed", "Error": "error", "NotExecuted": "skipped"}[case.attrib["outcome"]],
        )
        for case in root.iter()
        if case.tag.rsplit("}", 1)[-1] == "UnitTestResult"
    ]


def _structured_tests(spec: object) -> dict[str, dict[str, Any]]:
    if not isinstance(spec, dict) or set(spec) != {"format", "path", "run_record"}:
        raise ValueError("試験の組にはformat・path・run_recordを指定する")
    format_name = spec["format"]
    if not isinstance(format_name, str) or format_name not in {"vitest", "playwright", "trx"}:
        raise ValueError(f"未知の試験形式です: {format_name!r}。vitest・playwright・trxから指定する")
    path = path_value(spec["path"])
    record_path, record = run_record(spec["run_record"])
    argv, cwd = record["argv"], pathlib.Path(record["cwd"])
    worktree = verification_diagnostics.worktree_root(record) if format_name in {"vitest", "playwright"} else cwd
    if format_name == "vitest":
        outputs = [arg.removeprefix("--outputFile=") for arg in argv if arg.startswith("--outputFile=")]
        reporter = "--reporter=json" in argv or any(
            a == "--reporter" and b == "json" for a, b in zip(argv, argv[1:], strict=False)
        )
    elif format_name == "playwright":
        outputs = [
            arg.removeprefix("PLAYWRIGHT_JSON_OUTPUT_NAME=") for arg in argv if arg.startswith("PLAYWRIGHT_JSON_OUTPUT_NAME=")
        ]
        reporter = "env" in [pathlib.Path(arg).name for arg in argv] and (
            "--reporter=json" in argv or any(a == "--reporter" and b == "json" for a, b in zip(argv, argv[1:], strict=False))
        )
    else:
        loggers = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--logger=")]
        loggers.extend(b for a, b in zip(argv, argv[1:], strict=False) if a == "--logger")
        outputs = [
            part.removeprefix("LogFileName=")
            for logger in loggers
            if logger.startswith("trx;")
            for part in logger.split(";")
            if part.startswith("LogFileName=")
        ]
        reporter = True
    if (
        not reporter
        or len(outputs) != 1
        or not pathlib.Path(outputs[0]).is_absolute()
        or pathlib.Path(outputs[0]).resolve() != path.resolve()
    ):
        raise ValueError(
            f"試験結果と実行argvの出力指定が対応しません: {path}, {record_path}。絶対パスの構造化出力指定で再実行する"
        )
    try:
        if format_name == "trx":
            cases = _trx(path)
        else:
            data = load_json(path)
            cases = _vitest(data, cwd, worktree) if format_name == "vitest" else _playwright(data, worktree)
    except (KeyError, TypeError, AttributeError, ET.ParseError, json.JSONDecodeError) as error:
        raise ValueError(f"試験結果の形式・状態が不正です: {path}: {error}。選んだ形式の結果を再取得する") from error
    results = {}
    for number, (identity, status) in enumerate(cases, 1):
        if not isinstance(identity, str) or not identity or identity in results:
            raise ValueError(f"テスト完全名が一意ではありません: {path}: {identity!r}")
        results[identity] = {
            "test": identity,
            "status": status,
            "format": format_name,
            "path": str(path),
            "case": number,
            "run_record": str(record_path),
            "git_head": record["git_head"],
            "git_status": record["git_status"],
            "child_exit_code": record["child_exit_code"],
            "stdout": record["stdout_path"],
            "stderr": record["stderr_path"],
        }
    failed = any(value["status"] in {"failed", "error"} for value in results.values())
    if not results or record["child_exit_code"] not in {0, 1} or failed != (record["child_exit_code"] == 1):
        raise ValueError(f"試験結果の状態と子の終了コードが一致しません: {path}。結果と実行記録を再取得する")
    return results


def load_results(path: pathlib.Path | None, *, specification: dict[str, Any] | None = None) -> dict[str, Any]:
    """保存した試験結果と診断を読み、基準版の指定があれば差を求めて返す。診断の読込と比較はverification_diagnosticsが行う。"""
    if path is None and not specification:
        return {"tests": {}, "test_changes": [], "diagnostics": {}}
    spec = load_json(path) if path is not None else specification
    allowed = {"junit", "baseline_junit", "tests", "baseline_tests", "diagnostics", "baseline_diagnostics"}
    if not isinstance(spec, dict) or not spec or set(spec) - allowed:
        raise ValueError(
            "結果JSONにはtests・baseline_tests・junit・baseline_junit・diagnostics・baseline_diagnosticsの組を指定する"
        )
    if ({"tests", "baseline_tests"} & spec.keys()) and ({"junit", "baseline_junit"} & spec.keys()):
        raise ValueError("同じ版のtestsとjunitは別々に指定する")
    if "baseline_tests" in spec and "tests" not in spec:
        raise ValueError("基準版と比較する対象結果を指定する")
    if "baseline_junit" in spec and "junit" not in spec or "baseline_diagnostics" in spec and "diagnostics" not in spec:
        raise ValueError("基準版と比較する対象結果を指定する")
    tests = _junit(spec["junit"]) if "junit" in spec else {}
    baseline = _junit(spec["baseline_junit"]) if "baseline_junit" in spec else {}
    if "tests" in spec:
        tests = _structured_tests(spec["tests"])
    if "baseline_tests" in spec:
        baseline = _structured_tests(spec["baseline_tests"])
        if spec["tests"]["format"] != spec["baseline_tests"]["format"]:
            raise ValueError("比較する試験結果は同じ形式で指定する")
    changes = []
    if "baseline_junit" in spec or "baseline_tests" in spec:
        for name in sorted(tests.keys() | baseline.keys()):
            before, after = baseline.get(name), tests.get(name)
            if before is None or after is None or before["status"] != after["status"]:
                changes.append(
                    {
                        "test": name,
                        "change": "added" if before is None else "deleted" if after is None else "changed",
                        "before": before,
                        "after": after,
                    }
                )
    diagnostics: dict[str, Any] = {}
    if "diagnostics" in spec:
        diagnostics = verification_diagnostics.compare(spec["diagnostics"], spec.get("baseline_diagnostics"))
    return {"tests": tests, "test_changes": changes, "diagnostics": diagnostics}


def results_for_arguments(args: argparse.Namespace) -> dict[str, Any]:
    """対応する保存物を公開引数から組み立て、既存の取込みと比較へ渡す。"""
    specification: dict[str, Any] = {}
    for prefix, key in (("test", "tests"), ("baseline_test", "baseline_tests")):
        values = [getattr(args, prefix + suffix, None) for suffix in ("_format", "_file", "_record")]
        if all(value is None for value in values):
            continue
        if any(value is None for value in values):
            raise ValueError(f"{prefix}の形式・結果・実行記録は組で指定する")
        specification[key] = {"format": values[0], "path": str(values[1]), "run_record": str(values[2])}
    for prefix in ("junit", "baseline_junit"):
        xml = getattr(args, prefix + "_xml", None)
        record = getattr(args, prefix + "_record", None)
        if xml is None and record is None:
            continue
        if xml is None or record is None:
            raise ValueError(f"{prefix}のXMLと実行記録は組で指定する")
        specification[prefix] = {"xml": str(xml), "run_record": str(record)}
    for prefix in ("diagnostics", "baseline_diagnostics"):
        path, record, conditions = (getattr(args, prefix + suffix, None) for suffix in ("_file", "_record", "_conditions"))
        sources = getattr(args, prefix + "_source", None)
        if all(value is None for value in (path, record, conditions, sources)):
            continue
        if path is None or record is None or conditions is None:
            raise ValueError(f"{prefix}の診断・実行記録・保存条件は組で指定する")
        if sources and len({pair[0] for pair in sources}) != len(sources):
            raise ValueError(f"{prefix}の保存時本文は各ファイルへ一意に指定する")
        _record_path, observed = run_record(str(record))
        specification[prefix] = {
            "path": str(path),
            "run_record": str(record),
            "head": observed["git_head"],
            "conditions": load_json(conditions),
            "sources": dict(sources or []),
        }
    if specification and args.results_file is not None:
        raise ValueError("--results-fileと保存物の直接指定は別々に使う")
    return load_results(args.results_file, specification=specification)


def has_result_specification(args: argparse.Namespace) -> bool:
    """JSONか保存物の直接指定があるかを両消費側で同じ条件から判定する。"""
    return args.results_file is not None or any(
        getattr(args, prefix + suffix, None) is not None
        for prefixes, suffixes in (
            (("test", "baseline_test"), ("_format", "_file", "_record")),
            (("junit", "baseline_junit"), ("_xml", "_record")),
            (("diagnostics", "baseline_diagnostics"), ("_file", "_record", "_conditions", "_source")),
        )
        for prefix in prefixes
        for suffix in suffixes
    )


def _row_target_options(*, reviewing: bool) -> tuple[str, ...]:
    """更新先の行を第1要素に持つ行別指定の属性名を返す。レビュー更新の第2要素（継承元・受理済み行）は更新先ではない。"""
    options = ("row_evidence_file", "row_result_test", "row_result_diagnostic")
    return options + (("row_outcome", "row_source", "verification_row", "accepted_row") if reviewing else ())


def selected_rows(args: argparse.Namespace) -> list[str]:
    """両公開操作の更新先の行選択を解決する。

    `--select-row`を明示した場合は重複の有無を確かめる前の値をそのまま返し、省略した場合は行別指定の更新先を
    オプションごとの指定順に重複なく並べて返す。どちらも無ければ空リストを返す。
    表示・更新の判定と更新配列の組み立ては、この結果だけから行を選ぶ。
    """
    if getattr(args, "select_row", None):
        return list(args.select_row)
    return list(
        dict.fromkeys(pair[0] for option in _row_target_options(reviewing=True) for pair in getattr(args, option, None) or [])
    )


def _row_selector(payload: dict[str, Any], value: str) -> tuple[str, int]:
    section, separator, raw = value.partition(":")
    if not separator or section not in {"wi_conditions", "user_requirements"} or not raw.isdecimal():
        raise ValueError("行選択はwi_conditions:Nまたはuser_requirements:Nで指定する")
    number = int(raw)
    if not 1 <= number <= len(payload[section]):
        raise ValueError(f"選択行が存在しません: {value}。--listで行番号を確認する")
    return section, number


def updates_for_arguments(
    payload: dict[str, Any],
    args: argparse.Namespace,
    *,
    reviewing: bool = False,
    pending: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """選んだ行の原文・出所を現在の記録から取得し、選択した結果へ対応付ける。"""
    selectors = selected_rows(args)
    if not selectors:
        raise ValueError("更新する行を行別指定（--row-evidence-fileなど）か--select-rowで指定する")
    if len(selectors) != len(set(selectors)):
        raise ValueError("--select-rowは重複なく指定する")
    if args.mode not in {"append", "replace"}:
        raise ValueError("選択行の更新には--modeを指定する")
    if args.updates_file is not None:
        raise ValueError("行選択（--select-rowか行別指定）と--updates-fileは別々に使う")
    # 不正な行を判定の不足などの派生した診断より先に、その行の値として示す。
    for selector in selectors:
        _row_selector(payload, selector)
    row_files: dict[str, str] = {}
    row_tests: dict[str, list[str]] = collections.defaultdict(list)
    row_diagnostics: dict[str, list[str]] = collections.defaultdict(list)
    for option, common in (
        ("row_evidence_file", args.evidence_file),
        ("row_result_test", args.result_test),
        ("row_result_diagnostic", args.result_diagnostic),
    ):
        pairs = getattr(args, option, None) or []
        if pairs and common:
            raise ValueError(f"--{option.replace('_', '-')}と全選択行への共通指定は別々に使う")
        seen_pairs: set[tuple[str, str]] = set()
        for selector, value in pairs:
            if selector not in selectors or (selector, value) in seen_pairs:
                raise ValueError(
                    f"--{option.replace('_', '-')}は選択行（--select-rowを明示した場合はその集合）へ重複なく指定する"
                )
            seen_pairs.add((selector, value))
            if option == "row_evidence_file":
                if selector in row_files:
                    raise ValueError("--row-evidence-fileは各選択行へ一意に指定する")
                row_files[selector] = value
            elif option == "row_result_test":
                row_tests[selector].append(value)
            else:
                row_diagnostics[selector].append(value)
    outcome_pairs = getattr(args, "row_outcome", None) or []
    inherited_pairs = getattr(args, "verification_row", None) or []
    source_pairs = getattr(args, "row_source", None) or []
    outcomes = dict(outcome_pairs)
    inherited = dict(inherited_pairs)
    sources = dict(source_pairs)
    accepted_pairs = getattr(args, "accepted_row", None) or []
    accepted = dict(accepted_pairs)
    accepted_path = getattr(args, "accepted_evidence", None)
    if len(accepted) != len(accepted_pairs) or set(accepted) - set(selectors):
        raise ValueError("--accepted-rowは選択行（--select-rowを明示した場合はその集合）へ重複なく指定する")
    if bool(accepted) != (accepted_path is not None):
        raise ValueError("--accepted-rowと--accepted-evidenceは組で指定する")
    if accepted and (
        not reviewing
        or args.mode != "replace"
        or any(args.evidence_file is None and selector not in row_files for selector in accepted)
    ):
        raise ValueError("受理済み判定の参照はレビュー更新で--evidence-fileと--mode replaceを指定する")
    if len(outcomes) != len(outcome_pairs) or len(inherited) != len(inherited_pairs) or len(sources) != len(source_pairs):
        raise ValueError("行の判定と未判定行への対応は重複なく指定する")
    if reviewing and set(outcomes) != set(selectors):
        raise ValueError(
            f"--row-outcomeで全選択行の判定を明示する: 判定の無い行={sorted(set(selectors) - set(outcomes))}。"
            "選択行は--select-rowか、省略時は行別指定の更新先で決まる"
        )
    if set(inherited) - set(selectors):
        raise ValueError("--verification-rowの対応先は--select-rowで選んだ行を指定する")
    if set(sources) - set(selectors):
        raise ValueError("--row-sourceは--select-rowで選んだ行へ指定する")
    updates = []
    for selector in selectors:
        section, number = _row_selector(payload, selector)
        update: dict[str, Any] = {
            "section": section,
            "row": number,
            "source": payload[section][number - 1]["source"],
            "mode": args.mode,
            "tests": row_tests.get(selector, args.result_test or []),
            "diagnostics": row_diagnostics.get(selector, args.result_diagnostic or []),
        }
        if args.evidence_file is not None:
            update["evidence_file"] = str(args.evidence_file)
        elif selector in row_files:
            update["evidence_file"] = row_files[selector]
        if reviewing:
            update.update(outcome=outcomes[selector], reviewed_head=args.reviewed_head)
            if selector in sources:
                update["new_source"] = sources[selector]
            if selector in inherited:
                if pending is None:
                    raise ValueError("--verification-rowは--verification-recordと指定する")
                old_section, old_number = _row_selector(pending, inherited[selector])
                if old_section != section:
                    raise ValueError("未判定根拠は同じ配列の同じ要求単位を選ぶ")
                update["verification_source"] = pending[old_section][old_number - 1]["source"]
            if selector in accepted:
                source_selector = re.fullmatch(r"(wi_conditions|user_requirements):([0-9]+)", accepted[selector])
                if source_selector is None or source_selector[1] != section or selector in inherited:
                    raise ValueError("受理済み行は未判定根拠の継承と分け、同じ配列のSECTION:Nを指定する")
                update["accepted_source"] = {"path": str(accepted_path), "section": section, "row": int(source_selector[2])}
        updates.append(update)
    return updates


def validate_display(args: argparse.Namespace) -> None:
    """表示の選択を更新へ混入させず、要約と詳細の用途を分ける。"""
    selecting = bool(args.result_test or args.result_diagnostic)
    direct = bool(selected_rows(args))
    if args.results_summary and not args.list_results or selecting and not (args.list_results or direct):
        raise ValueError(
            "--results-summary・--result-test・--result-diagnosticは--list-resultsと指定する。"
            "--result-test・--result-diagnosticを全選択行の共通値として保存する場合は--select-rowで行を選ぶ"
        )
    if args.results_summary and selecting:
        raise ValueError("要約と指定結果の詳細は別々に表示する")
    if args.list_results and direct:
        raise ValueError("結果一覧と選択行の更新は別々に実行する")


def _summary(results: dict[str, Any]) -> dict[str, Any]:
    tests: dict[str, list[str]] = collections.defaultdict(list)
    for name, value in results["tests"].items():
        tests[value["status"]].append(name)
    changes: dict[tuple, list[str]] = collections.defaultdict(list)
    for value in results["test_changes"]:
        changes[(value["change"], (value["before"] or {}).get("status"), (value["after"] or {}).get("status"))].append(
            value["test"]
        )
    groups: dict[tuple, list[str]] = collections.defaultdict(list)
    for identity, value in results["diagnostics"].items():
        observed = value.get("after") if isinstance(value.get("after"), dict) else value.get("before")
        observed = observed if isinstance(observed, dict) else {}
        groups[
            (value["change"], *(observed.get(key) for key in ("emitter", "rule", "type", "message")), value.get("reason"))
        ].append(identity)
    return {
        "tests": [{"status": status, "count": len(names), "tests": names} for status, names in tests.items()],
        "test_changes": [
            {"change": key[0], "before_status": key[1], "after_status": key[2], "count": len(names), "tests": names}
            for key, names in changes.items()
        ],
        "diagnostics": [
            {
                **dict(zip(("change", "emitter", "rule", "severity", "message", "reason"), key, strict=True)),
                "count": len(ids),
                "diagnostics": ids,
            }
            for key, ids in groups.items()
        ],
    }


def list_results(results: dict[str, Any], args: argparse.Namespace) -> None:
    """一覧から担当が選べる識別子と所在を返す。"""
    if args.results_summary:
        displayed = _summary(results)
    elif args.result_test or args.result_diagnostic:
        names, identities = set(args.result_test or []), set(args.result_diagnostic or [])
        known_names = results["tests"].keys() | {item["test"] for item in results["test_changes"]}
        if names - known_names or identities - results["diagnostics"].keys():
            raise ValueError(
                f"指定した結果がありません: tests={sorted(names - known_names)}, "
                f"diagnostics={sorted(identities - results['diagnostics'].keys())}"
            )
        displayed = {
            "tests": {name: results["tests"][name] for name in results["tests"] if name in names},
            "test_changes": [item for item in results["test_changes"] if item["test"] in names],
            "diagnostics": {identity: value for identity, value in results["diagnostics"].items() if identity in identities},
        }
    else:
        displayed = results
    print(json.dumps(displayed, ensure_ascii=False, indent=2))


def _observed_evidence(values: list[dict[str, Any]]) -> str:
    """同じ保存物の参照を集合でまとめ、各結果の完全名・状態と固有の位置を残す。"""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    fields = ("path", "run_record", "child_exit_code", "stdout", "stderr")
    for value in values:
        if "test" in value:
            key = ("test", *(value[field] for field in fields))
        else:
            key = (
                "diagnostic",
                *(
                    value.get(side, {}).get(field) if isinstance(value.get(side), dict) else None
                    for side in ("before", "after")
                    for field in ("path", "run_record")
                ),
            )
        groups.setdefault(key, []).append(value)
    lines: list[str] = []
    for number, (key, items) in enumerate(groups.items(), start=1):
        lines.append(f"保存物の組{number}:")
        if key[0] == "test":
            lines.extend(f"{field}: `{items[0][field]}`" for field in fields)
            if "xml" in items[0]:
                lines.append(f"xml: `{items[0]['xml']}`")
            lines.extend(f"`{item['test']}` {item['status']}、case: `{item['case']}`" for item in items)
            continue
        for side in ("before", "after"):
            observed = items[0].get(side)
            if isinstance(observed, dict):
                lines.extend(f"{side}.{field}: `{observed[field]}`" for field in ("path", "run_record"))
        for item in items:
            lines.append(f"診断比較: {item['change']}")
            for side in ("before", "after"):
                observed = item.get(side)
                if isinstance(observed, dict):
                    lines.extend(f"{side}.{field}: `{observed[field]}`" for field in ("entry", "message_position"))
                elif isinstance(observed, str):
                    lines.append(f"{side}: `{observed}`")
            if "reason" in item:
                lines.append(f"比較不能の理由: {item['reason']}")
    return "\n".join(lines)


def updated_payload(
    payload: dict[str, Any],
    updates_path: pathlib.Path | None,
    results: dict[str, Any],
    *,
    reviewing: bool = False,
    pending: dict[str, Any] | None = None,
    outcomes: dict[str, Any] | None = None,
    updates: object | None = None,
    row_check: collections.abc.Callable[[str, int, dict[str, Any]], list[str]] | None = None,
) -> dict[str, Any]:
    """全行の契約を確かめてから複製へ反映し、不正入力では一行も保存しない。

    `row_check`は更新後の各行を配列名・1始まりの行番号とともに受け取り、その行の根拠の診断を返す。
    呼出元が返却前の確認と同じ参照の解決を渡し、1件でも診断があれば全件を示して一行も保存しない。
    本モジュールは返却前の確認のモジュールから読まれるため、その規則を直接importせず引数で受け取る。
    """
    if updates is not None and updates_path is not None:
        raise ValueError("更新JSONと直接行選択は別々に使う")
    if updates is None:
        updates = load_json(updates_path) if updates_path is not None else None
    if not isinstance(updates, list) or not updates:
        raise ValueError("更新JSONは空でない配列が必要です")
    result = copy.deepcopy(payload)
    seen: set[tuple[str, int]] = set()
    order: list[tuple[str, int]] = []
    allowed = {"section", "row", "source", "tests", "diagnostics", "evidence_file", "mode"}
    if reviewing:
        allowed |= {"outcome", "reviewed_head", "verification_source", "new_source", "accepted_source"}
    for update in updates:
        if not isinstance(update, dict) or set(update) - allowed or not {"section", "row", "source", "mode"} <= set(update):
            raise ValueError("更新行の項目が不正です。ヘルプの更新JSONの形式を確認する")
        section, number = update["section"], update["row"]
        if (
            not isinstance(section, str)
            or section not in {"wi_conditions", "user_requirements"}
            or not isinstance(number, int)
            or isinstance(number, bool)
            or not 1 <= number <= len(result[section])
        ):
            raise ValueError("sectionと1始まりのrowを一覧から指定する")
        if (section, number) in seen:
            raise ValueError(f"同じ要求行の更新が重複しています: {section}[{number}]")
        seen.add((section, number))
        order.append((section, number))
        row = result[section][number - 1]
        if (
            update["source"] != row["source"]
            or not isinstance(update["mode"], str)
            or update["mode"] not in {"append", "replace"}
        ):
            raise ValueError(f"出所か更新modeが一致しません: {section}[{number}]")
        evidence = []
        observed_results: list[dict[str, Any]] = []
        for field, collection in (("tests", results["tests"]), ("diagnostics", results["diagnostics"])):
            selected = update.get(field, [])
            if (
                not isinstance(selected, list)
                or any(not isinstance(item, str) for item in selected)
                or len(set(selected)) != len(selected)
            ):
                raise ValueError(f"{field}は重複のない識別子配列を指定する")
            for key in selected:
                if key not in collection:
                    raise ValueError(f"選択した{field}がありません: {key}。--list-resultsで確認する")
                observed_results.append(collection[key])
        if observed_results:
            evidence.append(_observed_evidence(observed_results))
        if "evidence_file" in update:
            evidence.append(path_value(update["evidence_file"]).read_text(encoding="utf-8").strip())
        if reviewing:
            if "accepted_source" in update:
                reference = update["accepted_source"]
                if (
                    update["mode"] != "replace"
                    or "evidence_file" not in update
                    or observed_results
                    or "verification_source" in update
                ):
                    raise ValueError("受理済み判定の参照は説明と置換だけで記録し、結果の取込み・未判定継承と分ける")
                if (
                    not isinstance(reference, dict)
                    or set(reference) != {"path", "section", "row"}
                    or reference["section"] != section
                    or not isinstance(reference["row"], int)
                    or isinstance(reference["row"], bool)
                    or not isinstance(reference["path"], str)
                ):
                    raise ValueError("accepted_sourceには同じ配列のpath・section・rowを指定する")
                if not evidence or not any(item.strip() for item in evidence):
                    raise ValueError("受理済み判定を参照する理由を空でない説明ファイルへ書く")
                evidence.insert(
                    0, accepted_review_evidence.reference(pathlib.Path(reference["path"]), section, reference["row"], row)
                )
            if (
                not isinstance(update.get("outcome"), str)
                or update.get("outcome") not in (outcomes or {}).get(section, set())
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(update.get("reviewed_head", ""))) is None
            ):
                raise ValueError("レビュー更新はoutcomeとreviewed_headの完全OIDを各行で明示する")
            if "verification_source" in update:
                source = update["verification_source"]
                matches = [r for r in (pending or {}).get(section, []) if r["source"] == source]
                if len(matches) != 1 or matches[0].get("outcome") or matches[0].get("reviewed_head"):
                    raise ValueError("選択した未判定根拠の出所が一意ではありません")
                original = "condition" if section == "wi_conditions" else "requirement"
                if matches[0][original] != row[original] or matches[0]["awi"] != row["awi"] or source != row["source"]:
                    raise ValueError("未判定根拠は同じ要求単位を選択する")
                evidence.append(matches[0]["evidence"])
            if "new_source" in update:
                if update["outcome"] not in {"失効", "割当外", "背景"}:
                    raise ValueError(
                        "new_sourceは失効・割当外・背景の判断根拠へ変更する場合だけ指定する。"
                        "延期・終端しない行はsourceを変えず、evidenceへ記録の所在を書く。"
                        "new_sourceまたは--row-sourceの指定を外し、所在を書いた説明ファイルを"
                        "更新JSONではevidence_file、CLIでは--evidence-fileへ指定する"
                    )
                if not isinstance(update["new_source"], str) or not update["new_source"].strip():
                    raise ValueError("判断根拠のnew_sourceは空でない所在を指定する")
                row["source"] = update["new_source"]
            row["outcome"], row["reviewed_head"] = update["outcome"], update["reviewed_head"]
        elif row.get("outcome") or row.get("reviewed_head"):
            raise ValueError("判定済み入力へ未判定根拠を保存できません")
        value = "\n".join(item for item in evidence if item)
        if not value:
            raise ValueError(f"根拠が空です: {section}[{number}]")
        row["evidence"] = "\n".join(item for item in (row["evidence"], value) if item) if update["mode"] == "append" else value
    if row_check is not None:
        errors = [error for section, number in order for error in row_check(section, number, result[section][number - 1])]
        if errors:
            raise reference_failure(errors)
    return result


def reference_failure(errors: list[str]) -> ValueError:
    """保存操作が解決できない参照を全件示して拒否する例外を、未判定検証記録と完成条件証拠の全ての更新で同じ文面にして返す。"""
    return ValueError(
        f"保存する根拠に解決できない参照が{len(errors)}件あるため、記録を変更していません。\n" + "\n".join(errors)
    )


def save(path: pathlib.Path, payload: dict[str, Any]) -> None:
    """検証済みの全更新を一度で保存する。"""
    if not path.is_absolute():
        raise ValueError("保存先は絶対パスで指定する")
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
