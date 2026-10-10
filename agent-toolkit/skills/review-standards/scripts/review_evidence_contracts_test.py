"""根拠の保存物のまとめ、全行表示とgit参照の保護を公開CLIから検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import check_exec_review_evidence as check
import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository as git


def _payload(head: str, bodies: list[str], *, unmet: bool = False) -> dict:
    return {
        "wi_conditions": [
            {
                "awi": f"20261010-100000-00{number}.md",
                "condition": f"契約{number}",
                "outcome": "未達" if unmet and number == 1 else "達成",
                "source": "入力原文",
                "evidence": body,
                "reviewed_head": head,
            }
            for number, body in enumerate(bodies, start=1)
        ],
        "user_requirements": [],
    }


def _inputs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, bodies: list[str], *, unmet: bool = False
) -> tuple[str, pathlib.Path, pathlib.Path]:
    repository = git.init_repository(tmp_path / "repo", commit_message="基準")
    head = git.git_output(repository, "rev-parse", "HEAD")
    monkeypatch.chdir(repository)
    inputs = tmp_path / "inputs.txt"
    inputs.write_text(
        "AWI: 20261010-100000-001.md\n判定対象: 契約1\n終端区分: 終端しない\n後続工程: 追加観測\n検収時機: 入力の取得後\n",
        encoding="utf-8",
    )
    payload = _payload(head, bodies, unmet=unmet)
    if unmet:
        payload["wi_conditions"][0]["source"] = str(inputs)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return head, inputs, evidence


def _return_args(tmp_path: pathlib.Path, head: str, inputs: pathlib.Path, evidence: pathlib.Path) -> list[str]:
    table = tmp_path / "review.tsv"
    table.touch()
    return [
        str(evidence),
        "--input-record",
        str(inputs),
        "--expected-head",
        head,
        "--review-table",
        str(table),
        "--round",
        "1",
        "--return-result",
        "--review-start",
        head,
        "--reader-fit-review",
        "文章成果物なし",
    ]


@pytest.mark.parametrize("unmet", [False, True])
def test_public_evidence_display_references_prior_equal_body(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, unmet: bool
) -> None:
    """同一根拠を一度で読み、非達成行との再掲・単一指定・通常返却の境界も保つ。"""
    body = "test_save_and_reload passed"
    head, inputs, evidence = _inputs(tmp_path, monkeypatch, [body, body], unmet=unmet)
    args = _return_args(tmp_path, head, inputs, evidence)
    assert check.main([*args, "--show-all-rows"]) == 0
    output = capsys.readouterr().out
    assert output.count(body) == 1
    displayed = [
        json.loads(line.removeprefix("証拠の全行: ")) for line in output.splitlines() if line.startswith("証拠の全行: ")
    ]
    assert displayed[1]["evidence_reference"]["section"] == "wi_conditions"
    assert displayed[1]["evidence_reference"]["row"] == 1
    if unmet:
        assert displayed[0]["evidence_reference"]["display"] == "達成以外の行"
    assert check.main([str(evidence), "--list"]) == 0
    assert capsys.readouterr().out.count(body) == 1
    assert check.main([str(evidence), "--list", "--section", "wi_conditions", "--row", "2"]) == 0
    single = json.loads(capsys.readouterr().out.removeprefix("証拠の全行: "))
    assert single["evidence"] == body and "evidence_reference" not in single
    assert check.main(args) == 0
    normal = capsys.readouterr().out
    assert "証拠の全行:" not in normal and "evidence_reference" not in normal


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1234567", "7654321"),
        ("abcdef1..1234567", "abcdef1..7654321"),
        ("abcdef1..a1b2c3d", "abcdef1..a9b8c7d"),
        ("abcdef1...a1b2c3d", "abcdef1...a9b8c7d"),
        ("abcdef1~1", "abcdef1~2"),
        ("abcdef1^1", "abcdef1^2"),
        ("abcdef1~1^2", "abcdef1~2^2"),
    ],
)
def test_public_shared_evidence_keeps_git_reference_forms(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], left: str, right: str
) -> None:
    """参照の数字だけの違いを、異なるWIの根拠の共用として拒否しない。"""
    head, inputs, evidence = _inputs(tmp_path, monkeypatch, [f"差分 {left} で観測した", f"差分 {right} で観測した"])
    assert check.main([str(evidence), "--input-record", str(inputs), "--expected-head", head]) == 0
    assert not capsys.readouterr().err
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["wi_conditions"][1]["evidence"] = payload["wi_conditions"][0]["evidence"]
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert check.main([str(evidence), "--input-record", str(inputs), "--expected-head", head]) == 1
    assert left in capsys.readouterr().err


def test_public_shared_evidence_still_rejects_integer_only_difference(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """参照の外の入力値の数字だけを替えた汎用根拠は従来どおり拒否する。"""
    head, inputs, evidence = _inputs(tmp_path, monkeypatch, ["入力値1を観測", "入力値2を観測"])
    assert check.main([str(evidence), "--input-record", str(inputs), "--expected-head", head]) == 1
    assert "共用" in capsys.readouterr().err


def _record_command(repository: pathlib.Path, *arguments: str) -> dict:
    """公開run-commandで有限のproducerを実行し、保存物の所在を返す。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_toolkit.atk",
            "run-command",
            "--cwd",
            str(repository),
            "--timeout",
            "30",
            "--",
            sys.executable,
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    saved = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert saved["child_exit_code"] == 0
    return saved


def _trial(repository: pathlib.Path, name: str, *, tracked: bool = False) -> dict[str, str]:
    """実pytestと公開run-commandで、2試験を共有する保存物を取得する。"""
    trial = repository / f"{name}_test.py"
    trial.write_text("def test_save():\n    assert 1 == 1\n\ndef test_reload():\n    assert 2 == 2\n", encoding="utf-8")
    if tracked:
        git.git_output(repository, "add", "--", trial.name)
        git.git_output(repository, "commit", "--no-verify", "-m", "試験の記録対象版")
    xml = repository / f"{name}.xml"
    saved = _record_command(
        repository, "-m", "pytest", "-v", "-p", "no:cacheprovider", "-o", "junit_family=xunit1", f"--junitxml={xml}", str(trial)
    )
    return {"xml": str(xml), "run_record": saved["record_path"]}


def _diagnostics(repository: pathlib.Path, directory: pathlib.Path, name: str) -> dict:
    """公開JSONL形式を有限のproducerから取得し、記録と保存時本文へ対応付ける。"""
    content = "first = 1\nsecond = 2\n"
    messages = [{"line": number, "col": 1, "severity": "warning", "rule": "W001", "msg": f"診断{number}"} for number in (1, 2)]
    values = [
        {"kind": "command", "command": "lint", "status": "succeeded"},
        {"kind": "diagnostic", "command": "lint", "file": "code.py", "messages": messages},
    ]
    producer = directory / f"{name}_producer.py"
    producer.write_text("print(" + repr("\n".join(json.dumps(item) for item in values)) + ")\n", encoding="utf-8")
    saved = _record_command(repository, str(producer))
    snapshot = directory / f"{name}_source.txt"
    snapshot.write_text(content, encoding="utf-8")
    return {
        "path": saved["stdout_path"],
        "run_record": saved["record_path"],
        "head": saved["git_head"],
        "conditions": {
            "scope": ["code.py"],
            "options": [],
            "dependencies": {},
            "parallelism": 1,
            "environment": {"OS": "検証"},
        },
        "sources": {"code.py": str(snapshot)},
    }


def test_public_selected_results_group_shared_artifacts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """両証拠CLIで共有するXML・実行記録を一度だけ書き、全試験名とcaseを残す。"""
    repository = git.init_repository(tmp_path / "repo", commit_message="基準")
    head = git.git_output(repository, "rev-parse", "HEAD")
    monkeypatch.chdir(repository)
    spec = _trial(repository, "trial")
    before = _diagnostics(repository, tmp_path, "before")
    after = _diagnostics(repository, tmp_path, "after")
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps({"junit": spec, "diagnostics": after, "baseline_diagnostics": before}), encoding="utf-8")
    names = [f"trial_test.py::{name}" for name in ("test_save", "test_reload")]
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps(
            {
                "wi_conditions": [
                    {
                        "awi": "20261010-100000-001.md",
                        "condition": "保存と再読込",
                        "outcome": "",
                        "source": "原文",
                        "evidence": "",
                        "reviewed_head": "",
                    }
                ],
                "user_requirements": [],
            }
        ),
        encoding="utf-8",
    )
    shared = [
        "--select-row",
        "wi_conditions:1",
        "--results-file",
        str(results_path),
        "--mode",
        "replace",
        "--result-test",
        names[0],
        "--result-test",
        names[1],
        "--result-diagnostic",
        "unchanged:1",
        "--result-diagnostic",
        "unchanged:2",
    ]
    assert (
        run_script.dispatch(
            argparse.Namespace(script_name="verification-record", script_args=["--output", str(pending), *shared])
        )
        == 0
    )
    capsys.readouterr()
    body = json.loads(pending.read_text(encoding="utf-8"))["wi_conditions"][0]["evidence"]
    assert body.count("xml:") == 1 and body.count("\nrun_record:") == 1
    assert body.count("before.run_record:") == 1 and body.count("after.run_record:") == 1
    assert body.count("before.message_position:") == 2 and body.count("after.message_position:") == 2
    assert body.index("xml:") < body.index(names[0]) < body.index("before.run_record:") < body.index("診断比較:")
    assert all(f"`{name}` passed" in body for name in names)
    assert "case: `1`" in body and "case: `2`" in body
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(pending.read_bytes())
    assert (
        check.main(
            [
                str(evidence),
                "--output",
                str(evidence),
                *shared,
                "--row-outcome",
                "wi_conditions:1",
                "達成",
                "--reviewed-head",
                head,
            ]
        )
        == 0
    )
    reviewed = json.loads(evidence.read_text(encoding="utf-8"))["wi_conditions"][0]["evidence"]
    assert reviewed == body


def test_public_grouped_evidence_keeps_reference_and_shared_checks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ保存結果の個別書式と集合書式が、返却生成と不在参照で同じ結果になる。"""
    head, inputs, evidence = _inputs(tmp_path, monkeypatch, ["仮の根拠", "仮の根拠"])
    spec = _trial(pathlib.Path.cwd(), "trial", tracked=True)
    head = git.git_output(pathlib.Path.cwd(), "rev-parse", "HEAD")
    names = [f"trial_test.py::{name}" for name in ("test_save", "test_reload")]
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"junit": spec}), encoding="utf-8")
    assert (
        check.main(
            [
                str(evidence),
                "--output",
                str(evidence),
                "--results-file",
                str(results),
                "--select-row",
                "wi_conditions:1",
                "--select-row",
                "wi_conditions:2",
                "--result-test",
                names[0],
                "--result-test",
                names[1],
                "--mode",
                "replace",
                "--row-outcome",
                "wi_conditions:1",
                "達成",
                "--row-outcome",
                "wi_conditions:2",
                "達成",
                "--reviewed-head",
                head,
            ]
        )
        == 0
    )
    grouped = json.loads(evidence.read_text(encoding="utf-8"))
    legacy = "\n".join(
        f"`{name}` passed\nxml: `{spec['xml']}`\nrun_record: `{spec['run_record']}`\ncase: `{index}`"
        for index, name in enumerate(names, start=1)
    )
    capsys.readouterr()
    for body in (legacy, grouped["wi_conditions"][0]["evidence"]):
        payload = json.loads(json.dumps(grouped))
        for row in payload["wi_conditions"]:
            row["evidence"] = body
        evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        args = _return_args(tmp_path, head, inputs, evidence)
        assert check.main(args) == 0, capsys.readouterr().err
        assert "状態: completed" in capsys.readouterr().out
        missing = body.replace(spec["xml"], str(tmp_path / "missing.xml"))
        for row in payload["wi_conditions"]:
            row["evidence"] = missing
        evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert check.main(args) == 1
        assert "missing.xml" in capsys.readouterr().err
