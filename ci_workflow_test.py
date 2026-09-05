"""CI workflowの所有権契約を静的に検証する。"""

import re
import typing
from pathlib import Path

import pytest
import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parent
_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yaml"
_RELEASE_CONDITION = (
    "github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name == github.repository && "
    "github.head_ref == 'develop' && "
    "github.base_ref == 'master'"
)
_OWNER_CONDITION = (
    "github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name != github.repository || "
    "github.head_ref != 'develop' || "
    "github.base_ref != 'master'"
)
_STATUSLINE_CONDITION = "github.event_name == 'pull_request' && github.base_ref == 'master'"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return typing.cast(dict[str, object], value)


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    value = job["steps"]
    assert isinstance(value, list)
    return [_mapping(step) for step in value]


def _jobs(workflow: dict[str, object]) -> dict[str, object]:
    return _mapping(workflow["jobs"])


def _common_jobs(workflow: dict[str, object]) -> list[dict[str, object]]:
    return [job for value in _jobs(workflow).values() if "(non-owner)" in str((job := _mapping(value)).get("name", ""))]


def _job_by_display_name(workflow: dict[str, object], name: str) -> dict[str, object]:
    matches = [job for job in _common_jobs(workflow) if name in str(job.get("name", ""))]
    assert len(matches) == 1
    return matches[0]


def _load_workflow() -> dict[str, object]:
    with _WORKFLOW_PATH.open(encoding="utf-8") as stream:
        # BaseLoaderはYAML 1.1の`on`キーを真偽値へ変換せず、安全なスカラー値だけを構築する。
        value = yaml.load(stream, Loader=yaml.BaseLoader)
    return _mapping(value)


@pytest.fixture(scope="module", name="workflow_data")
def _workflow_fixture() -> dict[str, object]:
    return _load_workflow()


def test_common_jobs_start_with_explicit_non_owner_marker(workflow_data: dict[str, object]) -> None:
    "次の設計契約を検証する。\n\n共通jobにはjob-level `if`を置かず、step-level条件で非所有markerと既存実処理を切り替える。\n同一repositoryのheadが`develop`、baseが`master`のpull requestでは、共通jobの先頭で非所有markerだけを成功させ、checkoutを含む既存実処理を実行しない。\nこのrelease pull request以外の同一repository pull requestとfork pull requestでは、非所有markerをskipして既存実処理を実行する。\n非所有markerはcheckout前から存在する`${{ github.workspace }}`を作業場所とし、`test-windows`は`pwsh`、その他の共通jobは`bash`を明示する。\n`rust-lint`の既存job既定作業場所は維持し、非所有markerだけがworkspace rootを明示して既定を上書きする。"  # noqa: E501
    windows_job = _job_by_display_name(workflow_data, "test-windows")

    for job in _common_jobs(workflow_data):
        assert "if" not in job
        steps = _steps(job)
        marker = steps[0]
        assert marker["if"] == _RELEASE_CONDITION
        assert marker["working-directory"] == "${{ github.workspace }}"
        assert marker["shell"] == ("pwsh" if job is windows_job else "bash")

        for step in steps[1:]:
            condition = typing.cast(str, step.get("if", ""))
            assert _OWNER_CONDITION in condition
            assert _RELEASE_CONDITION not in condition

    rust_job = _job_by_display_name(workflow_data, "rust-lint")
    rust_defaults = _mapping(rust_job["defaults"])
    assert "working-directory" in _mapping(rust_defaults["run"])
    assert all("working-directory" not in step for step in _steps(rust_job)[1:])


def test_common_job_display_names_keep_owner_and_non_owner_names(workflow_data: dict[str, object]) -> None:
    """「共通CIは`push`と`master`向け`pull_request`の全経路でjobを開始し、job表示名とmatrixを評価する。」を検証する。

    表の「共通5 job」「5件のrequired check名」「5件の`(non-owner)`名」も入力とする。
    """
    for job in _common_jobs(workflow_data):
        display_name = typing.cast(str, job["name"])
        branch_names = re.findall(r"'([^']+)'", display_name.partition(_RELEASE_CONDITION)[2])
        assert _RELEASE_CONDITION in display_name
        assert any("(non-owner)" in branch_name for branch_name in branch_names)
        assert any("(non-owner)" not in branch_name for branch_name in branch_names)

    python_job = _job_by_display_name(workflow_data, "python-lint")
    strategy = _mapping(python_job["strategy"])
    matrix = _mapping(strategy["matrix"])
    assert len(typing.cast(list[object], matrix["python-version"])) == 2


def test_statusline_version_is_an_independent_master_pull_request_check(
    workflow_data: dict[str, object],
) -> None:
    "次の設計契約を検証する。\n\n`statusline-version`は`pull_request`かつbaseが`master`の全pull requestで実行し、head repository、head branch及びrelease条件を追加の限定に使わない。\n同一repositoryのrelease及びnon-release pull requestとfork pull requestが同じ検査対象となり、`rust-lint`というrequired名の重複を生成しない。\nruleset `21524717`のrequired checkは共通5名と`statusline-version`の6件とし、既存5名を変更しない。"  # noqa: E501
    statusline_jobs = [
        job for value in _jobs(workflow_data).values() if (job := _mapping(value)).get("name") == "statusline-version"
    ]
    assert len(statusline_jobs) == 1
    statusline_job = statusline_jobs[0]
    assert statusline_job["if"] == _STATUSLINE_CONDITION
    statusline_condition = typing.cast(str, statusline_job["if"])  # type: ignore[redundant-cast]
    assert "head.repo" not in statusline_condition
    assert "head_ref" not in statusline_condition
    assert _RELEASE_CONDITION not in statusline_condition
    assert statusline_job["name"] != _job_by_display_name(workflow_data, "rust-lint")["name"]


def test_job_and_step_cardinality(workflow_data: dict[str, object]) -> None:
    """表の「共通5 job」と次の設計契約を検証する。

    ruleset `21524717`のrequired checkは共通5名と`statusline-version`の6件とし、既存5名を変更しない。
    """
    common_jobs = _common_jobs(workflow_data)
    expanded_common_job_count = 0
    for job in common_jobs:
        expansion_count = 1
        strategy = job.get("strategy")
        if isinstance(strategy, dict):
            matrix = _mapping(strategy).get("matrix")
            if isinstance(matrix, dict):
                for dimension, values in _mapping(matrix).items():
                    if dimension not in {"include", "exclude"} and isinstance(values, list):
                        expansion_count *= len(values)
        expanded_common_job_count += expansion_count

    statusline_jobs = [
        job for value in _jobs(workflow_data).values() if (job := _mapping(value)).get("name") == "statusline-version"
    ]
    assert expanded_common_job_count == 5
    assert len(statusline_jobs) == 1
    assert expanded_common_job_count + len(statusline_jobs) == 6
