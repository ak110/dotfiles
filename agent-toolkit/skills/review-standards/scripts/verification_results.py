"""保存した試験結果と実行版を読み、担当が選んだ要求行へ根拠を関連付ける。"""

from __future__ import annotations

import argparse
import collections
import copy
import json
import pathlib
import re
import xml.etree.ElementTree as ET
from typing import Any

import verification_diagnostics
from verification_diagnostics import path_value, run_record

from agent_toolkit._common.atomic_file import atomic_write

SCHEMA = (
    '結果JSON: {"junit":{"xml":"/絶対/XML","run_record":"/絶対/record.json"},'
    '"baseline_junit":{同形式},"diagnostics":{"path":"/絶対/JSONL","run_record":"/絶対/record.json",'
    '"head":"完全OID","conditions":{"scope":[],"options":[],"dependencies":{},"parallelism":1,"environment":{}},'
    '"sources":{"相対ファイル":"/絶対/保存時本文"}},"baseline_diagnostics":{同形式}}。'
    '不要な組は省略できる。更新JSONは配列: [{"section":"wi_conditions","row":1,"source":"一覧の完全一致値",'
    '"tests":["相対file::Class::test[param]"],"diagnostics":["added:1"],'
    '"evidence_file":"/絶対/説明.txt","mode":"append"}]。'
    "レビューはoutcomeとreviewed_headも各行で明示し、verification_sourceに未判定記録の出所を指定できる。判定は結果から自動生成しない。"
)


def add_arguments(parser: argparse.ArgumentParser, *, reviewing: bool = False) -> None:
    """両公開操作へ同じ入力契約を登録する。"""
    parser.epilog = SCHEMA
    parser.add_argument("--results-file", type=pathlib.Path, metavar="PATH", help="保存した結果の組を持つJSONの絶対パス")
    parser.add_argument("--list-results", action="store_true", help="テスト完全名・状態・版・所在と基準版との差を表示")
    parser.add_argument(
        "--results-summary", action="store_true", help="--list-resultsで状態・差・診断のまとまりと全識別子を表示"
    )
    parser.add_argument(
        "--result-test", action="append", metavar="NAME", help="--list-resultsでこの完全テスト名の詳細だけを読む。反復可"
    )
    parser.add_argument(
        "--result-diagnostic", action="append", metavar="ID", help="--list-resultsでこの診断識別子の詳細だけを読む。反復可"
    )
    parser.add_argument(
        "--updates-file", type=pathlib.Path, metavar="PATH", help="出所と選択した根拠を持つ更新配列JSONの絶対パス"
    )
    if reviewing:
        parser.add_argument(
            "--verification-record", type=pathlib.Path, metavar="PATH", help="選択できる未判定検証記録の絶対パス"
        )
        parser.add_argument(
            "--output", type=pathlib.Path, metavar="PATH", help="未判定記録と異なる完成条件証拠の絶対パス。入力証拠を更新できる"
        )


def load_json(path: pathlib.Path) -> Any:
    """UTF-8の保存JSONを読む。"""
    return json.loads(path_value(str(path)).read_text(encoding="utf-8"))


def _junit(spec: object) -> dict[str, dict[str, Any]]:
    if not isinstance(spec, dict) or set(spec) != {"xml", "run_record"}:
        raise ValueError("JUnitの組にはxmlとrun_recordを指定する")
    xml = path_value(spec["xml"])
    record_path, record = run_record(spec["run_record"])
    argv = record["argv"]
    xml_args = [v.split("=", 1)[1] for v in argv if v.startswith("--junitxml=")]
    xml_args.extend(argv[i + 1] for i, v in enumerate(argv[:-1]) if v == "--junitxml")
    if len(xml_args) != 1 or (pathlib.Path(record["cwd"]) / xml_args[0]).resolve() != xml.resolve():
        raise ValueError(f"XMLと実行argvの--junitxmlが対応しません: {xml}, {record_path}")
    if "junit_family=xunit1" not in argv:
        raise ValueError(f"file属性を保存するjunit_family=xunit1で再実行する: {record_path}")
    stdout = path_value(record["stdout_path"]).read_text(encoding="utf-8")
    roots = re.findall(r"^rootdir: (.+)$", stdout, re.MULTILINE)
    if len(roots) != 1 or not pathlib.Path(roots[0]).is_absolute():
        raise ValueError(f"保存出力のpytest実行rootを一意に特定できません: {record_path}")
    try:
        prefix = pathlib.Path(roots[0]).relative_to(record["cwd"])
    except ValueError as error:
        raise ValueError(f"pytest実行rootが記録cwdの外です: {roots[0]}") from error
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


def load_results(path: pathlib.Path | None) -> dict[str, Any]:
    """保存試験と診断を所有するモジュールで読み、差を付ける。"""
    if path is None:
        return {"tests": {}, "test_changes": [], "diagnostics": {}}
    spec = load_json(path)
    allowed = {"junit", "baseline_junit", "diagnostics", "baseline_diagnostics"}
    if not isinstance(spec, dict) or not spec or set(spec) - allowed:
        raise ValueError("結果JSONにはjunit・baseline_junit・diagnostics・baseline_diagnosticsの組を指定する")
    if "baseline_junit" in spec and "junit" not in spec or "baseline_diagnostics" in spec and "diagnostics" not in spec:
        raise ValueError("基準版と比較する対象結果を指定する")
    tests = _junit(spec["junit"]) if "junit" in spec else {}
    baseline = _junit(spec["baseline_junit"]) if "baseline_junit" in spec else {}
    changes = []
    if "baseline_junit" in spec:
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


def validate_display(args: argparse.Namespace) -> None:
    """表示の選択を更新へ混入させず、要約と詳細の用途を分ける。"""
    selecting = bool(args.result_test or args.result_diagnostic)
    if (args.results_summary or selecting) and not args.list_results:
        raise ValueError("--results-summary・--result-test・--result-diagnosticは--list-resultsと指定する")
    if args.results_summary and selecting:
        raise ValueError("要約と指定結果の詳細は別々に表示する")


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


def _observed_evidence(value: dict[str, Any]) -> str:
    """既存の参照書式で保存結果を指し、版と詳細は取得時の記録に保持する。"""
    if "test" in value:
        lines = [f"`{value['test']}` {value['status']}"]
        fields = ("xml", "case", "run_record", "child_exit_code", "stdout", "stderr")
    else:
        lines = [f"診断比較: {value['change']}"]
        fields = ()
        for side in ("before", "after"):
            observed = value.get(side)
            if isinstance(observed, dict):
                # ファイル・行・本文は保存診断のentryとmessage_positionから読む。
                # 改名・削除前のfileを現在版の参照として解決させない。
                lines.extend(f"{side}.{key}: `{observed[key]}`" for key in ("path", "entry", "message_position", "run_record"))
            elif isinstance(observed, str):
                lines.append(f"{side}: `{observed}`")
        if "reason" in value:
            lines.append(f"比較不能の理由: {value['reason']}")
    lines.extend(f"{key}: `{value[key]}`" for key in fields)
    return "\n".join(lines)


def updated_payload(
    payload: dict[str, Any],
    updates_path: pathlib.Path,
    results: dict[str, Any],
    *,
    reviewing: bool = False,
    pending: dict[str, Any] | None = None,
    outcomes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """全行の契約を確かめてから複製へ反映し、不正入力では一行も保存しない。"""
    updates = load_json(updates_path)
    if not isinstance(updates, list) or not updates:
        raise ValueError("更新JSONは空でない配列が必要です")
    result = copy.deepcopy(payload)
    seen: set[tuple[str, int]] = set()
    allowed = {"section", "row", "source", "tests", "diagnostics", "evidence_file", "mode"}
    if reviewing:
        allowed |= {"outcome", "reviewed_head", "verification_source"}
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
        row = result[section][number - 1]
        if (
            update["source"] != row["source"]
            or not isinstance(update["mode"], str)
            or update["mode"] not in {"append", "replace"}
        ):
            raise ValueError(f"出所か更新modeが一致しません: {section}[{number}]")
        evidence = []
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
                evidence.append(_observed_evidence(collection[key]))
        if "evidence_file" in update:
            evidence.append(path_value(update["evidence_file"]).read_text(encoding="utf-8").strip())
        if reviewing:
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
                if matches[0][original] != row[original] or matches[0]["awi"] != row["awi"]:
                    raise ValueError("未判定根拠は同じ要求単位を選択する")
                evidence.append(matches[0]["evidence"])
            row["outcome"], row["reviewed_head"] = update["outcome"], update["reviewed_head"]
        elif row.get("outcome") or row.get("reviewed_head"):
            raise ValueError("判定済み入力へ未判定根拠を保存できません")
        value = "\n".join(item for item in evidence if item)
        if not value:
            raise ValueError(f"根拠が空です: {section}[{number}]")
        row["evidence"] = "\n".join(item for item in (row["evidence"], value) if item) if update["mode"] == "append" else value
    return result


def save(path: pathlib.Path, payload: dict[str, Any]) -> None:
    """検証済みの全更新を一度で保存する。"""
    if not path.is_absolute():
        raise ValueError("保存先は絶対パスで指定する")
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
