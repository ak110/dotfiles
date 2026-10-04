"""既存成果物と、生成・登録される契約との整合を検証する。"""

import shlex

import pytest

from ci_workflow_test import (  # pylint: disable=unused-import  # 共有fixtureをpytestへ登録する
    _REPOSITORY_ROOT,
    _direct_pytest_targets,
    _flag_options,
    _jobs,
    _mapping,
    _steps,
    _workflow_fixture,  # noqa: F401  # pylint: disable=unused-import
)


@pytest.mark.repo_invariant
def test_direct_pytest_targets_exist(workflow_data: dict[str, object]) -> None:
    """workflowのpytestコマンドが直接指定するリポジトリ内の対象は実在する。"""
    targets = _direct_pytest_targets(workflow_data)
    assert targets
    for target in targets:
        # pytestのnode指定はファイルパスの後に::でクラス名やテスト名を持つ。
        assert (_REPOSITORY_ROOT / target.split("::", maxsplit=1)[0]).exists(), target


@pytest.mark.repo_invariant
def test_windows_launcher_environment_is_built_before_boundary_tests(workflow_data: dict[str, object]) -> None:
    """実ランチャー到達テストより前に、ランチャーと同じ指定でagent-toolkit環境を構築する。

    構築がテスト中に起きると、Windowsランナーの導入時間がテストごとの60秒上限へ算入される。
    """
    steps = _steps(_mapping(_jobs(workflow_data)["test-windows"]))
    launcher_test = "check_exec_review_evidence_test.py::test_public_command_runs_platform_launcher"
    test_index = next(i for i, step in enumerate(steps) if launcher_test in str(step.get("run", "")))
    sync_indexes = [
        i
        for i, step in enumerate(steps)
        if shlex.split(str(step.get("run", "")))[:4] == ["uv", "sync", "--project", "agent-toolkit"]
    ]
    assert len(sync_indexes) == 1
    sync_index = sync_indexes[0]
    assert sync_index < test_index
    sync_step, test_step = steps[sync_index], steps[test_index]
    for key in ("if", "shell", "working-directory"):
        assert sync_step.get(key) == test_step.get(key), key

    # ランチャーと指定が異なるとuv runが環境を再作成し、事前構築が無効になる。
    launcher_lines = [
        line
        for line in (_REPOSITORY_ROOT / "agent-toolkit" / "bin" / "atk.cmd").read_text(encoding="utf-8").splitlines()
        if line.startswith("uv run --project")
    ]
    assert len(launcher_lines) == 1
    assert _flag_options(shlex.split(str(sync_step["run"]))) == _flag_options(shlex.split(launcher_lines[0]))
