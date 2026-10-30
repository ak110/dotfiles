"""公開CLIで固定素材の分離、機能未達と試行不成立を検証する。"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._testing.isolation import restore_host_environment

SCRIPT = pathlib.Path(__file__).with_name("multi_turn_trials.py")
FIXTURES = SCRIPT.parents[1] / "fixtures" / "multi-turn"
CASES = ("attachment-paths", "weekday-queries", "batch-conditions", "display-spaces", "verification-status")


@pytest.fixture(autouse=True)
def _host(monkeypatch: pytest.MonkeyPatch) -> None:
    # CLIは設定を読まず、全書込先はtmp_pathから明示する。
    restore_host_environment(monkeypatch)


def _run(*arguments: str, script: pathlib.Path = SCRIPT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(script), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        timeout=30,
        check=False,
    )


def _grade(tmp_path: pathlib.Path, case: str, example: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
    candidate = tmp_path / "candidate.py"
    record_kind = "dialogue" if "dialogue" in example else "execution"
    record = tmp_path / f"{record_kind}.json"
    result = tmp_path / "result.json"
    candidate.write_text(example["candidate"], encoding="utf-8")
    record.write_text(json.dumps(example[record_kind], ensure_ascii=False), encoding="utf-8")
    process = _run(
        "grade",
        "--case",
        case,
        "--condition",
        example["condition"],
        "--candidate",
        str(candidate),
        f"--{record_kind}",
        str(record),
        "--output",
        str(result),
    )
    return process, json.loads(result.read_text(encoding="utf-8")) if result.exists() else {}


@pytest.mark.parametrize("case", CASES)
def test_prepare_copies_only_public_material_and_refuses_overwrite(tmp_path: pathlib.Path, case: str) -> None:
    """準備が公開素材だけを複製し、既存の提出物を上書きしないことを確かめる。"""
    output = tmp_path / "public"
    process = _run("prepare", "--case", case, "--output", str(output))
    assert process.returncode == 0, process.stderr
    assert {path.name for path in output.iterdir()} == {"task.py", "request.md"}
    task = output / "task.py"
    task.write_text("提出物\n", encoding="utf-8")
    process = _run("prepare", "--case", case, "--output", str(output))
    assert process.returncode == 2
    assert task.read_text(encoding="utf-8") == "提出物\n"


@pytest.mark.parametrize("case", CASES)
def test_saved_success_and_failure_are_graded_from_artifacts(tmp_path: pathlib.Path, case: str) -> None:
    """保存した成功例と失敗例を、成果物の機能テストで判定する。"""
    trial = json.loads((FIXTURES / case.replace("-", "_") / "private" / "trial.json").read_text(encoding="utf-8"))
    for number, example in enumerate(trial["examples"]):
        directory = tmp_path / str(number)
        directory.mkdir()
        process, result = _grade(directory, case, example)
        assert process.returncode == (0 if example["name"] == "success" else 1), process.stderr
        assert result["status"] == "成立"
        if trial["kind"] == "dialogue":
            assert result["behavior"] == example["dialogue"]["behavior"]
        else:
            assert not result["behavior"]
            assert "user" not in result["provenance"]["sessions"]
            assert "dialogue_path" not in result
            assert result["provenance"]["engine"] == example["execution"]["engine"]
        assert {row["id"] for row in result["requirements"]} == {row["id"] for row in trial["requirements"]}
        assert result["test_run"]["exit_code"] == 0
        failed = {row["id"] for row in result["functional"] if not row["passed"]}
        expected = {
            "attachment-paths": {"associate", "delete", "legacy"},
            "weekday-queries": {"legacy-function", "legacy-query", "public-iso"},
            "batch-conditions": {"mixed", "order"},
            "display-spaces": {"spaces", "edges"},
            "verification-status": {"skipped", "not-collected", "xfail", "unknown"},
        }
        assert failed == (expected[case] if example["name"] == "failure" else set())
        assert {row["id"]: row["status"] for row in result["requirements"]} == {
            row["id"]: "未達" if failed.intersection(row["tests"]) else "達成" for row in trial["requirements"]
        }


@pytest.mark.parametrize("violation", ["unknown", "unasked", "internal", "audit", "execution"])
def test_disclosure_and_execution_failures_are_not_candidate_failures(tmp_path: pathlib.Path, violation: str) -> None:
    """一覧外要求・先回り・内部情報・監査違反・障害を不成立として保存する。"""
    trial = json.loads((FIXTURES / "weekday_queries" / "private" / "trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][0]
    dialogue = example["dialogue"]
    if violation == "unknown":
        dialogue["disclosures"][0]["requirement"] = "new-request"
    elif violation == "unasked":
        dialogue["disclosures"][0]["matched"] = False
    elif violation == "internal":
        dialogue["disclosures"][0]["source"] = "grade"
    elif violation == "audit":
        dialogue["disclosure_audit"]["violations"] = ["初期依頼に正解を先回り開示"]
    else:
        dialogue["termination"] = "error"
        dialogue["execution_errors"] = ["モデル起動失敗"]
    process, result = _grade(tmp_path, "weekday-queries", example)
    assert process.returncode == 2, process.stderr
    assert result["status"] == "不成立"
    assert result["execution_errors"]
    assert not result["functional"]
    assert not result["requirements"]


def test_no_followup_disclosure_is_invalid_even_with_correct_artifact(tmp_path: pathlib.Path) -> None:
    """追加回答を返さない条件で開示した対話は、成果物が正しくても試行不成立とする。"""
    trial = json.loads((FIXTURES / "display_spaces" / "private" / "trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][2]
    example["condition"] = "no-followup"
    example["dialogue"]["condition"] = "no-followup"
    process, result = _grade(tmp_path, "display-spaces", example)
    assert process.returncode == 2, process.stderr
    assert result["status"] == "不成立"


def test_behavior_quote_requires_real_turn_evidence(tmp_path: pathlib.Path) -> None:
    """存在しない対話引用を根拠とする合格記録を拒否する。"""
    trial = json.loads((FIXTURES / "batch_conditions" / "private" / "trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][0]
    example["dialogue"]["behavior"][0]["evidence"][0]["quote"] = "存在しない発話"
    process, result = _grade(tmp_path, "batch-conditions", example)
    assert process.returncode == 2
    assert not result
    assert "根拠" in process.stderr


def test_syntax_error_is_unmet_and_hang_is_inconclusive(tmp_path: pathlib.Path) -> None:
    """提出コードの欠陥と、成果物テストの実行障害を分ける。"""
    trial = json.loads((FIXTURES / "display_spaces" / "private" / "trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][0]
    example["candidate"] = "def broken(:\n"
    process, result = _grade(tmp_path, "display-spaces", example)
    assert process.returncode == 1, process.stderr
    assert result["status"] == "成立"
    assert all(not check["passed"] for check in result["functional"])
    (tmp_path / "candidate.py").write_text("while True: pass\n", encoding="utf-8")
    process = _run(
        "grade",
        "--case",
        "display-spaces",
        "--candidate",
        str(tmp_path / "candidate.py"),
        "--dialogue",
        str(tmp_path / "dialogue.json"),
        "--output",
        str(tmp_path / "result.json"),
        "--timeout",
        "0.1",
    )
    assert process.returncode == 2
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "不成立"
    assert result["test_run"]["exit_code"] is None


def test_public_list_and_finite_termination(tmp_path: pathlib.Path) -> None:
    """課題の公開一覧と、往復数の上限で終えた対話に残った未達の判定を検証する。"""
    process = _run("--list")
    assert process.returncode == 0, process.stderr
    rows = json.loads(process.stdout)
    assert {row["case"] for row in rows} == set(CASES)
    assert {row["case"]: row["kind"] for row in rows} == {
        case: "single" if case == "verification-status" else "dialogue" for case in CASES
    }
    trial = json.loads((FIXTURES / "batch_conditions" / "private" / "trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][1]
    dialogue = example["dialogue"]
    dialogue["termination"] = "turn-limit"
    dialogue["turns"].extend(
        [{"role": "user", "text": "対応を続けてください。"}, {"role": "candidate", "text": "変更中です。"}] * 4
    )
    dialogue["disclosure_audit"]["reviewed_user_turns"] = [1, 3, 5, 7, 9, 11]
    process, result = _grade(tmp_path, "batch-conditions", example)
    assert process.returncode == 1, process.stderr
    assert result["status"] == "成立"
    assert any(row["status"] == "未達" for row in result["requirements"])


@pytest.mark.parametrize("field", ["document_version", "engine", "model", "settings", "sessions", "termination", "case"])
def test_single_requires_execution_provenance(tmp_path: pathlib.Path, field: str) -> None:
    """実施情報の不足は、単独課題の機能未達へ混入させない。"""
    trial = json.loads((FIXTURES / "verification_status/private/trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][0]
    del example["execution"][field]
    process, result = _grade(tmp_path, "verification-status", example)
    assert process.returncode == 2
    assert not result
    assert process.stderr


@pytest.mark.parametrize("termination,errors", [("error", []), ("submitted", ["モデル起動失敗"])])
def test_single_execution_failure_is_inconclusive(tmp_path: pathlib.Path, termination: str, errors: list[str]) -> None:
    """成果物が正しくても起動・実行障害を試行不成立にする。"""
    trial = json.loads((FIXTURES / "verification_status/private/trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][0]
    example["execution"].update(termination=termination, execution_errors=errors)
    process, result = _grade(tmp_path, "verification-status", example)
    assert process.returncode == 2, process.stderr
    assert result["status"] == "不成立"
    assert result["execution_errors"]
    assert not result["functional"]


@pytest.mark.parametrize("field,value", [("settings", {}), ("sessions", {}), ("engine", 1), ("execution_errors", "障害")])
def test_single_rejects_malformed_execution_metadata(tmp_path: pathlib.Path, field: str, value: object) -> None:
    """起動情報の型や必須の内側項目が不正なら、採点を始めず入力の修正を求める。"""
    trial = json.loads((FIXTURES / "verification_status/private/trial.json").read_text(encoding="utf-8"))
    example = trial["examples"][0]
    example["execution"][field] = value
    process, result = _grade(tmp_path, "verification-status", example)
    assert process.returncode == 2
    assert process.stderr
    assert not result


@pytest.mark.parametrize("case,flag", [("weekday-queries", "--execution"), ("verification-status", "--dialogue")])
def test_record_input_must_match_trial_kind(tmp_path: pathlib.Path, case: str, flag: str) -> None:
    """課題種別に合う入力だけを受理し、誤った入力に必要な引数を案内する。"""
    process = _run(
        "grade", "--case", case, "--candidate", "unused.py", flag, "unused.json", "--output", str(tmp_path / "result.json")
    )
    assert process.returncode == 2
    assert ("--dialogue" if flag == "--execution" else "--execution") in process.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("kind", [None, "unknown"])
def test_undeclared_trial_kind_is_rejected(tmp_path: pathlib.Path, kind: str | None) -> None:
    """種別未宣言を対話課題へ暗黙変換せず、保存素材の修正を求める。"""
    script = tmp_path / "scripts/multi_turn_trials.py"
    script.parent.mkdir()
    script.write_bytes(SCRIPT.read_bytes())
    directory = tmp_path / "fixtures/multi-turn/verification_status/private"
    directory.mkdir(parents=True)
    (directory / "trial.json").write_text(json.dumps({"kind": kind}), encoding="utf-8")
    process = _run("prepare", "--case", "verification-status", "--output", str(tmp_path / "public"), script=script)
    assert process.returncode == 2
    assert "kind" in process.stderr
    assert not (tmp_path / "public").exists()


@pytest.mark.parametrize("flags", [[], ["--dialogue", "unused.json", "--execution", "unused.json"]])
def test_grade_requires_exactly_one_record(tmp_path: pathlib.Path, flags: list[str]) -> None:
    """実施情報の欠落と対話入力との混在を公開CLIで拒否する。"""
    process = _run(
        "grade", "--case", "verification-status", "--candidate", "unused.py", "--output", str(tmp_path / "result.json"), *flags
    )
    assert process.returncode == 2
    assert process.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("target", ["candidate.py", "execution.json"])
def test_single_result_cannot_overwrite_inputs(tmp_path: pathlib.Path, target: str) -> None:
    """単独作業でも提出物と実施情報を結果の出力先にできない。"""
    trial = json.loads((FIXTURES / "verification_status/private/trial.json").read_text(encoding="utf-8"))
    process, _ = _grade(tmp_path, "verification-status", trial["examples"][0])
    assert process.returncode == 0, process.stderr
    protected = tmp_path / target
    original = protected.read_bytes()
    rejected = _run(
        "grade",
        "--case",
        "verification-status",
        "--candidate",
        str(tmp_path / "candidate.py"),
        "--execution",
        str(tmp_path / "execution.json"),
        "--output",
        str(protected),
    )
    assert rejected.returncode == 2
    assert protected.read_bytes() == original
