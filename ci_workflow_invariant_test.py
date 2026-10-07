"""`.github/workflows/`のpytestコマンドが指定する対象の実在と、Windowsのagent-toolkit環境の事前構築が`agent-toolkit/bin/atk.cmd`の起動指定と一致することを検証する。"""

import shlex

import pytest

from _test_helpers import (
    REPO_ROOT,
    direct_pytest_targets,
    flag_options,
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
        assert (REPO_ROOT / target.split("::", maxsplit=1)[0]).exists(), target


def test_windows_launcher_environment_is_built_before_boundary_tests(workflow_data: dict[str, object]) -> None:
    """実ランチャー到達テストより前に、ランチャーと同じ指定でagent-toolkit環境を構築する。

    構築がテスト中に起きると、Windowsランナーの導入時間がテストごとの60秒上限へ算入される。
    """
    steps = workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows"]))
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
        for line in (REPO_ROOT / "agent-toolkit" / "bin" / "atk.cmd").read_text(encoding="utf-8").splitlines()
        if line.startswith("uv run --project")
    ]
    assert len(launcher_lines) == 1
    assert flag_options(shlex.split(str(sync_step["run"]))) == flag_options(shlex.split(launcher_lines[0]))
