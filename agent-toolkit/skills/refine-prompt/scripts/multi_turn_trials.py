"""固定した多ターン試行の公開素材を準備し、保存対話と成果物を採点する。"""

import argparse
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
from collections.abc import Callable
from typing import Any

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "multi-turn"
CASES = ("attachment-paths", "weekday-queries", "batch-conditions", "display-spaces", "verification-status")


def grade_fixture(candidate: str, specifications: list[tuple[str, Callable[[types.ModuleType], Any], Any]]) -> None:
    """固定課題の公開操作を実行し、期待値との比較をJSONで返す。"""
    spec = importlib.util.spec_from_file_location("trial_candidate", pathlib.Path(candidate))
    assert spec is not None and spec.loader is not None
    task = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(task)
    except Exception as error:  # 提出コードの構文・import失敗を機能未達として扱う境界。
        load_error = repr(error)
    else:
        load_error = None
    checks = []
    for name, call, expected in specifications:
        row: dict[str, Any] = {"id": name, "expected": expected}
        if load_error is not None:
            row.update(passed=False, error=load_error)
        else:
            try:
                actual = call(task)
            except Exception as error:  # 提出物の公開操作が投げた例外を各固定テストへ帰属させる境界。
                row.update(passed=False, error=repr(error))
            else:
                row.update(passed=actual == expected, actual=actual)
        checks.append(row)
    print(json.dumps(checks, ensure_ascii=False))


def _read(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSONのオブジェクトが必要です: {path}。入力形式を修正してください")
    return value


def _trial(case: str) -> dict[str, Any]:
    trial = _read(FIXTURES / case.replace("-", "_") / "private" / "trial.json")
    if trial.get("kind") not in ("dialogue", "single"):
        raise ValueError(f"課題{case}のkindをdialogueまたはsingleとしてtrial.jsonに宣言してください")
    return trial


def _execution(data: dict[str, Any], trial: dict[str, Any], condition: str) -> list[str]:
    if data.get("case") != trial["case"] or data.get("condition") != condition:
        raise ValueError("課題または条件が一致しません。同じ条件の実施情報を指定してください")
    for name in ("document_version", "engine", "model"):
        if not isinstance(data.get(name), str) or not data[name].strip():
            raise ValueError(f"{name}が必要です。実際の起動記録を入力してください")
    settings = data.get("settings")
    if not isinstance(settings, dict) or not isinstance(settings.get("effort"), str) or not settings["effort"].strip():
        raise ValueError("settings.effortが必要です。実際の起動設定を入力してください")
    sessions = data.get("sessions")
    if not isinstance(sessions, dict) or not isinstance(sessions.get("candidate"), str) or not sessions["candidate"].strip():
        raise ValueError("sessions.candidateが必要です。評価対象の実session識別子を入力してください")
    errors = data.get("execution_errors", [])
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("execution_errorsは障害の文字列配列へ修正してください")
    if data.get("termination") not in ("submitted", "error"):
        raise ValueError("単独作業のterminationはsubmittedまたはerrorです。終了を観測して記録してください")
    if data["termination"] == "error" or errors:
        return [
            *errors,
            "起動または実行障害。実施情報のsessions.candidateが指す起動記録で障害を確認し、"
            "原因を解消して同一条件で試行を再実行してください",
        ]
    return []


def _reference(turns: list[dict[str, Any]], reference: dict[str, Any], role: str) -> int:
    if not isinstance(reference, dict):
        raise ValueError("引用はturnとquoteを持つオブジェクトへ修正してください")
    number = reference.get("turn")
    quote = reference.get("quote")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or not 1 <= number <= len(turns)
        or not isinstance(quote, str)
        or not quote.strip()
        or turns[number - 1]["role"] != role
        or quote not in turns[number - 1]["text"]
    ):
        raise ValueError("対話の根拠が該当ターンと一致しません。役割・番号・逐語引用を修正してください")
    return number


def _dialogue(data: dict[str, Any], trial: dict[str, Any], condition: str) -> tuple[list[str], list[dict[str, Any]]]:
    turns = data.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("全対話のturnsが必要です。初期依頼から終了まで保存してください")
    for number, turn in enumerate(turns):
        if (
            not isinstance(turn, dict)
            or turn.get("role") != ("user" if number % 2 == 0 else "candidate")
            or not isinstance(turn.get("text"), str)
            or not turn["text"].strip()
        ):
            raise ValueError("turnsはuserから交互に保存してください。欠けた応答を回収してください")
    for name in ("document_version", "model", "settings", "sessions"):
        if not data.get(name):
            raise ValueError(f"{name}が必要です。実際の起動記録を入力してください")
    if not isinstance(data["settings"], dict):
        raise ValueError("settingsは実際の起動設定を持つオブジェクトへ修正してください")
    sessions = data["sessions"]
    if not isinstance(sessions, dict) or not sessions.get("candidate") or not sessions.get("user"):
        raise ValueError("両役のsession識別子が必要です。実際の起動記録を入力してください")
    if sessions["candidate"] == sessions["user"]:
        raise ValueError("両役は独立したsessionが必要です。別々に起動してください")
    if data.get("case") != trial["case"] or data.get("condition") != condition:
        raise ValueError("課題または開示条件が一致しません。同じ条件の対話を指定してください")
    errors = data.get("execution_errors", [])
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("execution_errorsは障害の文字列配列へ修正してください")
    errors = list(errors)
    termination = data.get("termination")
    rounds = sum(turn["role"] == "candidate" for turn in turns)
    if termination == "error":
        errors.append("起動または実行障害")
    elif termination not in ("submitted", "turn-limit"):
        raise ValueError("terminationはsubmitted・turn-limit・errorです。終了を観測して記録してください")
    elif (termination == "turn-limit" and rounds != trial["max_rounds"]) or rounds > trial["max_rounds"]:
        errors.append("固定した往復数と終了条件の違反")
    if termination != "error" and turns[-1]["role"] != "candidate":
        errors.append("評価対象の最終応答が未観測。候補の最終応答まで記録し、同じ評価をやり直してください")
    requirements = {item["id"]: item for item in trial["requirements"]}
    disclosures = data.get("disclosures")
    if not isinstance(disclosures, list):
        raise ValueError("disclosuresが必要です。開示が無い場合は空配列を記録してください")
    for disclosure in disclosures:
        if not isinstance(disclosure, dict):
            raise ValueError("開示記録をオブジェクトへ修正してください")
        requirement = requirements.get(disclosure.get("requirement"))
        if requirement is None or condition not in requirement["disclose_in"]:
            errors.append("一覧外要求または開示条件違反")
            continue
        question = _reference(turns, disclosure["question"], "candidate")
        answer = _reference(turns, disclosure["answer"], "user")
        if answer != question + 1 or disclosure.get("matched") is not True:
            errors.append("質問に対応しない先回り開示")
        if disclosure.get("source") != requirement["source"]:
            errors.append("内部原因または採点結果の漏出")
    audit = data.get("disclosure_audit")
    if not isinstance(audit, dict) or audit.get("reviewed_user_turns") != [
        number + 1 for number, turn in enumerate(turns) if turn["role"] == "user"
    ]:
        raise ValueError("全userターンの開示監査が必要です。先回り開示と一覧外要求を判定してください")
    if not isinstance(audit.get("violations"), list) or any(not isinstance(item, str) for item in audit["violations"]):
        raise ValueError("開示監査のviolationsは違反の文字列配列へ修正してください")
    if audit.get("violations"):
        errors.extend(audit["violations"])
    judgments = data.get("behavior")
    if not isinstance(judgments, list) or any(not isinstance(item, dict) for item in judgments):
        raise ValueError("behaviorが必要です。全対話から基準ごとの判定を記録してください")
    indexed = {item["criterion"]: item for item in judgments}
    if set(indexed) != {item["id"] for item in trial["behavior_criteria"]} or len(indexed) != len(judgments):
        raise ValueError("対話基準が不足または重複しています。trial.jsonの全基準を判定してください")
    for judgment in judgments:
        if judgment.get("verdict") not in ("適合", "不適合", "未判定") or not judgment.get("reason"):
            raise ValueError("対話判定の値と理由が必要です。全対話を読んで記入してください")
        evidence = judgment.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("対話判定の根拠が必要です。該当ターンと逐語引用を記入してください")
        for reference in evidence:
            if not isinstance(reference, dict):
                raise ValueError("対話根拠はrole・turn・quoteのオブジェクトへ修正してください")
            _reference(turns, reference, reference.get("role", "candidate"))
    return errors, judgments


def _grade(args: argparse.Namespace, trial: dict[str, Any]) -> tuple[dict[str, Any], int]:
    record_path = args.dialogue if trial["kind"] == "dialogue" else args.execution
    data = _read(record_path)
    if trial["kind"] == "dialogue":
        errors, behavior = _dialogue(data, trial, args.condition)
    else:
        errors, behavior = _execution(data, trial, args.condition), []
    result: dict[str, Any] = {
        "case": args.case,
        "condition": args.condition,
        "status": "不成立" if errors else "成立",
        "execution_errors": errors,
        "provenance": {name: data[name] for name in ("document_version", "model", "settings", "sessions", "termination")},
        "candidate_path": str(args.candidate.resolve()),
        "behavior": behavior,
        "functional": [],
        "requirements": [],
    }
    result["dialogue_path" if trial["kind"] == "dialogue" else "execution_path"] = str(record_path.resolve())
    if trial["kind"] == "single":
        result["provenance"]["engine"] = data["engine"]
    if errors:
        return result, 2
    # 評価対象の提出物は専用複製で明示実行する。隔離ディレクトリはsandboxではない。
    with tempfile.TemporaryDirectory(prefix="grade-", dir=args.output.parent) as directory:
        candidate = pathlib.Path(directory) / "task.py"
        shutil.copyfile(args.candidate, candidate)
        command = [
            sys.executable,
            "-I",
            "-B",
            str(FIXTURES / args.case.replace("-", "_") / "private" / "grade.py"),
            str(candidate),
        ]
        try:
            proc = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            result.update(
                status="不成立",
                execution_errors=["成果物テストのtimeout"],
                test_run={"argv": command, "exit_code": None, "stdout": str(exc.stdout), "stderr": str(exc.stderr)},
            )
            return result, 2
        result["test_run"] = {"argv": command, "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        if proc.returncode != 0:
            result.update(status="不成立", execution_errors=["採点処理の実行障害。test_runの診断を確認して再試行してください"])
            return result, 2
        try:
            checks = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result.update(
                status="不成立",
                execution_errors=["採点出力がJSONとして読めません。test_runの両出力を確認してください"],
            )
            return result, 2
        result["functional"] = checks
        by_id = {item["id"]: item for item in checks}
        for requirement in trial["requirements"]:
            selected = [by_id[test] for test in requirement["tests"]]
            result["requirements"].append(
                {
                    "id": requirement["id"],
                    "status": "達成" if all(item["passed"] for item in selected) else "未達",
                    "tests": requirement["tests"],
                }
            )
        return result, 0 if all(item["passed"] for item in checks) else 1


def main(argv: list[str] | None = None) -> int:
    """CLIの準備または採点を実行し、終了状態を返す。"""
    parser = argparse.ArgumentParser(description="固定課題の準備と保存対話・成果物の採点")
    parser.add_argument("--list", action="store_true", help="課題名と開示条件をJSONで表示")
    commands = parser.add_subparsers(dest="command", help="公開素材を準備するprepare、提出物を採点するgrade")
    prepare = commands.add_parser("prepare", help="公開依頼とtask.pyだけを新しい領域へ複製")
    prepare.add_argument("--case", choices=CASES, required=True, help="--listで表示した固定課題名")
    prepare.add_argument(
        "--output", type=pathlib.Path, metavar="DIR", required=True, help="公開素材を複製する新規ディレクトリのパス"
    )
    grade = commands.add_parser("grade", help="保存した対話と提出物を採点")
    grade.add_argument("--case", choices=CASES, required=True, help="--listで表示した固定課題名")
    grade.add_argument("--condition", help="開示条件。省略時は課題の最初の条件")
    grade.add_argument("--candidate", type=pathlib.Path, metavar="PATH", required=True, help="提出されたtask.pyのパス")
    records = grade.add_mutually_exclusive_group(required=True)
    records.add_argument("--dialogue", type=pathlib.Path, metavar="PATH", help="対話課題の全対話と判定を保存したJSONのパス")
    records.add_argument("--execution", type=pathlib.Path, metavar="PATH", help="単独課題の実施情報を保存したJSONのパス")
    grade.add_argument("--output", type=pathlib.Path, metavar="PATH", required=True, help="採点結果を出力するJSONのパス")
    grade.add_argument("--timeout", type=float, default=10, help="成果物テストの有限秒数")
    args = parser.parse_args(argv)
    try:
        if args.list:
            if args.command:
                parser.error("--listは単独で指定してください")
            print(
                json.dumps(
                    [
                        {
                            "case": name,
                            "kind": (trial := _trial(name))["kind"],
                            "conditions": trial["conditions"],
                        }
                        for name in CASES
                    ],
                    ensure_ascii=False,
                )
            )
            return 0
        if not args.command:
            parser.error("--list、prepareまたはgradeを指定してください")
        trial = _trial(args.case)
        if args.command == "prepare":
            shutil.copytree(FIXTURES / args.case.replace("-", "_") / "public", args.output)
            print(json.dumps({"case": args.case, "public_dir": str(args.output.resolve())}, ensure_ascii=False))
            return 0
        args.condition = args.condition or trial["conditions"][0]
        if args.condition not in trial["conditions"]:
            parser.error("この課題の開示条件を--listから選んでください")
        if trial["kind"] == "dialogue" and args.dialogue is None:
            parser.error("対話課題には--dialogueを指定してください")
        if trial["kind"] == "single" and args.execution is None:
            parser.error("単独課題には--executionを指定してください")
        if not 0 < args.timeout < float("inf"):
            parser.error("--timeoutには正の有限秒数を指定してください")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        record_path = args.dialogue if trial["kind"] == "dialogue" else args.execution
        if args.output.resolve() in (args.candidate.resolve(), record_path.resolve()):
            parser.error("--outputは提出物と採点入力以外のパスを指定してください")
        result, code = _grade(args, trial)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": result["status"], "result_path": str(args.output.resolve())}, ensure_ascii=False))
        return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"試行の入力または採点が不正です: {exc}。保存入力と素材を確認して再実行してください", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
