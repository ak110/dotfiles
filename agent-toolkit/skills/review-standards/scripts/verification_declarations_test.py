"""結果宣言を使うラッパー・コンテナの公開取込と、実行後の改変拒否を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

import pytest
import verification_diagnostics

from agent_toolkit import atk
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository


def _result(kind: str) -> str:
    if kind == "junit":
        return '<testsuite><testcase file="sample_test.py" classname="sample_test" name="test_ok"/></testsuite>'
    if kind == "trx":
        return '<TestRun><Results><UnitTestResult testName="test_ok" outcome="Passed"/></Results></TestRun>'
    if kind == "vitest":
        data = {
            "testResults": [
                {
                    "name": "/app/e2e/sample_test.py",
                    "assertionResults": [
                        {"title": "test_ok", "ancestorTitles": [], "status": "passed"},
                    ],
                }
            ]
        }
    else:
        data = {
            "config": {"rootDir": "/app/e2e"},
            "suites": [
                {
                    "title": "sample_test.py",
                    "specs": [
                        {
                            "file": "sample_test.py",
                            "title": "test_ok",
                            "tests": [{"projectName": "chromium", "status": "expected"}],
                        },
                    ],
                }
            ],
        }
    return json.dumps(data)


def _import(tmp_path: pathlib.Path, kind: str, result: pathlib.Path, record: pathlib.Path) -> int:
    arguments = ["--output", str(tmp_path / "pending.json"), "--list-results", "--results-summary"]
    if kind == "junit":
        arguments.extend(["--junit-xml", str(result), "--junit-record", str(record)])
    else:
        arguments.extend(["--test-format", kind, "--test-file", str(result), "--test-record", str(record)])
    return run_script.dispatch(argparse.Namespace(script_name="verification-record", script_args=arguments))


@pytest.mark.parametrize("kind", ["playwright", "junit", "vitest", "trx"])
@pytest.mark.parametrize("wrapper", ["python", "make"])
def test_public_import_declared_wrapper_results(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    wrapper: str,
) -> None:
    """argvに出力指定のない実ラッパーで書いた結果を、宣言とパス対応から取り込む。"""
    make = shutil.which("make")
    if wrapper == "make" and make is None:
        pytest.skip("makeが導入されていない環境")
    repo = git_repository.init_repository(tmp_path / "repo")
    (repo / "e2e").mkdir()
    (repo / "e2e/sample_test.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    git_repository.commit_all(repo, "試験の入力")
    result = repo / "result.txt"
    script = repo / "write_result.py"
    script.write_text(
        "import pathlib\n"
        + f"pathlib.Path({str(result)!r}).write_text({_result(kind)!r}, encoding='utf-8')\n"
        + "print('rootdir: /app/e2e')\n",
        encoding="utf-8",
    )
    command = [sys.executable, str(script)]
    if wrapper == "make":
        (repo / "Makefile").write_text('test:\n\t"$(PYTHON)" write_result.py\n', encoding="utf-8")
        assert make is not None
        command = [make, "test", f"PYTHON={sys.executable}"]
    with pytest.raises(SystemExit) as completed:
        atk.main(
            ["run-command", "--cwd", str(repo), "--result-file", str(result), "--path-map", f"/app={repo}", "--", *command]
        )
    assert completed.value.code == 0
    summary = json.loads(capsys.readouterr().out)
    record = pathlib.Path(summary["record_path"])
    assert _import(tmp_path, kind, result, record) == 0, capsys.readouterr().err
    listed = json.loads(capsys.readouterr().out)
    expected = "test_ok" if kind == "trx" else "e2e/sample_test.py::test_ok"
    if kind == "playwright":
        expected += '::"chromium"'
    assert listed["tests"] == [{"status": "passed", "count": 1, "tests": [expected]}]
    result.write_text("changed after execution", encoding="utf-8")
    assert _import(tmp_path, kind, result, record) == 1
    assert "実行終了後に変更" in capsys.readouterr().err


def test_declared_existing_result_must_change(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """実行前から同じ内容で存在した結果を、新しい実行の結果として受理しない。"""
    repo = git_repository.init_repository(tmp_path / "repo")
    (repo / "input.txt").write_text("input", encoding="utf-8")
    git_repository.commit_all(repo, "基準")
    result = repo / "result.json"
    result.write_text(_result("playwright"), encoding="utf-8")
    with pytest.raises(SystemExit) as completed:
        atk.main(["run-command", "--cwd", str(repo), "--result-file", str(result), "--", sys.executable, "-c", "pass"])
    assert completed.value.code == 0
    record = pathlib.Path(json.loads(capsys.readouterr().out)["record_path"])
    assert _import(tmp_path, "playwright", result, record) == 1
    assert "実行の間に作成・更新されていません" in capsys.readouterr().err


def test_path_mapping_uses_longest_component_prefix(tmp_path: pathlib.Path) -> None:
    """接頭辞をパスの要素で比較し、似た名前の兄弟パスへ対応を適用しない。"""
    record = {
        "path_maps": [{"child": "/app", "host": str(tmp_path)}, {"child": "/app/tests", "host": str(tmp_path / "specific")}]
    }
    assert verification_diagnostics.mapped_path("/app/tests/a.py", record) == tmp_path / "specific/a.py"
    assert verification_diagnostics.mapped_path("/application/a.py", record) == pathlib.Path("/application/a.py")
