"""CI workflowの静的契約を検証する。"""

import re
import shlex
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


def _direct_pytest_targets(workflow: dict[str, object]) -> list[str]:
    targets: list[str] = []
    for value in _jobs(workflow).values():
        for step in _steps(_mapping(value)):
            command = step.get("run")
            if not isinstance(command, str) or "pytest" not in command.split():
                continue
            tokens = shlex.split(command)
            if "pytest" not in tokens:
                continue
            pytest_index = tokens.index("pytest")
            targets.extend(
                token
                for token in tokens[pytest_index + 1 :]
                if not token.startswith("-") and ("/" in token or token.endswith(".py"))
            )
    return targets


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

    表の「共通6 job」「6件のrequired check名」「6件の`(non-owner)`名」も入力とする。
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
    "次の設計契約を検証する。\n\n`statusline-version`は`pull_request`かつbaseが`master`の全pull requestで実行し、head repository、head branch及びrelease条件を追加の限定に使わない。\n同一repositoryのrelease及びnon-release pull requestとfork pull requestが同じ検査対象となり、`rust-lint`というrequired名の重複を生成しない。\nruleset `21524717`のrequired checkは共通6名と`statusline-version`の7件とし、`statusline-version`以外は共通CIのjob表示名と一致させる。"  # noqa: E501
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
    """表の「共通6 job」と次の設計契約を検証する。

    ruleset `21524717`のrequired checkは共通6名と`statusline-version`の7件とし、
    `statusline-version`以外は共通CIのjob表示名と一致させる。
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
    assert expanded_common_job_count == 6
    assert len(statusline_jobs) == 1
    assert expanded_common_job_count + len(statusline_jobs) == 7


def test_browser_e2e_job_runs_playwright_tests_on_python_314(workflow_data: dict[str, object]) -> None:
    """次の設計契約を検証する。

    ブラウザーE2Eは共通`python-lint`から分離した`browser-e2e` jobが所有し、Python 3.14で実行する。
    `python-lint`はChromiumの導入とブラウザーE2Eを実行しない。
    """
    browser_job = _job_by_display_name(workflow_data, "browser-e2e")
    assert _mapping(browser_job["env"])["UV_PYTHON"] == "3.14"
    browser_commands = [str(step.get("run", "")) for step in _steps(browser_job)]
    assert any("make setup-browser" in command for command in browser_commands)
    assert any("_atk/serve/browser_test.py" in command for command in browser_commands)

    python_lint_job = _job_by_display_name(workflow_data, "python-lint")
    python_lint_commands = [str(step.get("run", "")) for step in _steps(python_lint_job)]
    assert all("setup-browser" not in command for command in python_lint_commands)
    assert all("browser_test.py" not in command for command in python_lint_commands)

    browser_test_enabled_values = {"1", "true", "yes", "on"}
    python_lint_environments = [_mapping(python_lint_job.get("env", {}))]
    python_lint_environments.extend(_mapping(step.get("env", {})) for step in _steps(python_lint_job))
    assert all(
        str(environment.get("AGENT_TOOLKIT_SERVE_BROWSER_TESTS", "")).lower() not in browser_test_enabled_values
        for environment in python_lint_environments
    )


def test_direct_pytest_targets_exist(workflow_data: dict[str, object]) -> None:
    """workflowのpytestコマンドが直接指定するリポジトリ内の対象は実在する。"""
    targets = _direct_pytest_targets(workflow_data)
    assert targets
    for target in targets:
        assert (_REPOSITORY_ROOT / target).exists(), target
