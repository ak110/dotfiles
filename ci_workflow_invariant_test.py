"""`.github/workflows/`のpytestコマンドの対象の実在と、途中終了時の結果保持を検証する。"""

import ast
import shlex

import pytest

from _test_helpers import (
    REPO_ROOT,
    direct_pytest_targets,
    load_workflow,
    workflow_jobs,
    workflow_mapping,
    workflow_steps,
)


@pytest.fixture(scope="module", name="workflow_data")
def _workflow_fixture() -> dict[str, object]:
    return load_workflow()


def test_direct_pytest_targets_exist(workflow_data: dict[str, object]) -> None:
    """workflowのpytestコマンドが直接指定するリポジトリ内の対象は実在する。"""
    targets = direct_pytest_targets(workflow_data)
    assert targets
    for target in targets:
        # pytestのnode指定はファイルパスの後に::でクラス名やテスト名を持つ。
        filename, *selectors = target.split("::")
        path = REPO_ROOT / filename
        assert path.exists(), target
        if selectors:
            body = ast.parse(path.read_text(encoding="utf-8")).body
            for selector in selectors:
                definitions = {
                    node.name: node for node in body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                }
                name = selector.split("[", maxsplit=1)[0]
                assert name in definitions, target
                body = definitions[name].body


def _pytest_arguments(command: str) -> list[str] | None:
    """stepのコマンドがpytestを起動する場合は、pytestへ渡る引数を返す。"""
    tokens = shlex.split(command)
    if "pytest" in tokens:
        return tokens[tokens.index("pytest") + 1 :]
    if "pyfltr" in tokens and "--commands=pytest" in tokens:
        return [
            argument
            for token in tokens
            if token.startswith("--pytest-args=")
            for argument in shlex.split(token.removeprefix("--pytest-args="))
        ]
    return None


def test_pytest_steps_report_each_completed_test(workflow_data: dict[str, object]) -> None:
    """CIのpytestは完了した試験ごとに名前と結果を出力し、外側の打ち切りでも失敗した試験名を保存ログへ残す。

    簡略進捗は失敗した試験名をsession末尾の要約だけへ出力するため、時間上限やjobの停止で要約へ到達しないと特定できない。
    """
    commands = [
        str(step["run"])
        for job in workflow_jobs(workflow_data).values()
        for step in workflow_steps(workflow_mapping(job))
        if isinstance(step.get("run"), str)
    ]
    arguments = [parsed for command in commands if (parsed := _pytest_arguments(command)) is not None]
    assert arguments
    for parsed in arguments:
        assert any(argument in {"-v", "-vv", "--verbose"} for argument in parsed), parsed
