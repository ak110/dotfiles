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
CASES = ("attachment-paths", "weekday-queries", "batch-conditions", "display-spaces")


@pytest.fixture(autouse=True)
def _host(monkeypatch: pytest.MonkeyPatch) -> None:
    # CLIは設定を読まず、全書込先はtmp_pathから明示する。
    restore_host_environment(monkeypatch)


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        timeout=30,
        check=False,
    )


def _grade(tmp_path: pathlib.Path, case: str, example: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
    candidate = tmp_path / "candidate.py"
    dialogue = tmp_path / "dialogue.json"
    result = tmp_path / "result.json"
    candidate.write_text(example["candidate"], encoding="utf-8")
    dialogue.write_text(json.dumps(example["dialogue"], ensure_ascii=False), encoding="utf-8")
    process = _run(
        "grade",
        "--case",
        case,
        "--condition",
        example["condition"],
        "--candidate",
        str(candidate),
        "--dialogue",
        str(dialogue),
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
        assert len(result["behavior"]) == len(trial["behavior_criteria"])
        assert {row["id"] for row in result["requirements"]} == {row["id"] for row in trial["requirements"]}
        assert result["test_run"]["exit_code"] == 0
        if example["name"] == "failure":
            assert any(row["status"] == "未達" for row in result["requirements"])
            failed = {row["id"] for row in result["functional"] if not row["passed"]}
            expected = {
                "attachment-paths": {"legacy"},
                "weekday-queries": {"legacy-query", "public-iso"},
                "batch-conditions": {"mixed", "order"},
                "display-spaces": {"spaces", "edges"},
            }
            assert expected[case] <= failed


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
