"""CI workflowの所有権契約を静的に検証する。"""

import typing
from pathlib import Path

import pytest
import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parent
_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yaml"
_CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
_MISE_ACTION = "jdx/mise-action@c2a87611a18de5b3828c5652fe268e992400cb5c"
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
_MARKER_NAME = "共通CI非所有経路"
_MARKER_MESSAGE = "developからmasterへのrelease pull requestではdevelop pushが共通CIを所有する。"
_COMMON_JOB_IDS = ("test-linux", "test-windows", "python-lint", "rust-lint")
_PYTHON_VERSIONS = ("3.13", "3.14")
_PYTHON_313_CONDITION = f"({_OWNER_CONDITION}) && matrix.python-version == '3.13'"
_PYTHON_314_CONDITION = f"({_OWNER_CONDITION}) && matrix.python-version == '3.14'"


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return typing.cast(dict[str, object], value)


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    value = job["steps"]
    assert isinstance(value, list)
    return [_mapping(step) for step in value]


def _jobs(workflow: dict[str, object]) -> dict[str, object]:
    return _mapping(workflow["jobs"])


def _load_workflow() -> dict[str, object]:
    with _WORKFLOW_PATH.open(encoding="utf-8") as stream:
        # BaseLoaderはYAML 1.1の`on`キーを真偽値へ変換せず、安全なスカラー値だけを構築する。
        value = yaml.load(stream, Loader=yaml.BaseLoader)
    return _mapping(value)


@pytest.fixture(scope="module", name="workflow_data")
def _workflow_fixture() -> dict[str, object]:
    return _load_workflow()


def test_trigger_is_limited_to_push_and_master_pull_request(workflow_data: dict[str, object]) -> None:
    triggers = _mapping(workflow_data["on"])
    push = _mapping(triggers["push"])
    pull_request = _mapping(triggers["pull_request"])

    assert push["branches"] == ["**"]
    assert push["tags-ignore"] == ["*"]
    assert pull_request["branches"] == ["master"]


def test_common_jobs_start_with_explicit_non_owner_marker(workflow_data: dict[str, object]) -> None:
    jobs = _jobs(workflow_data)
    expected = {
        "test-linux": (8, "bash", f'echo "{_MARKER_MESSAGE}"'),
        "test-windows": (9, "pwsh", f'Write-Output "{_MARKER_MESSAGE}"'),
        "python-lint": (13, "bash", f'echo "{_MARKER_MESSAGE}"'),
        "rust-lint": (7, "bash", f'echo "{_MARKER_MESSAGE}"'),
    }

    for job_id, (step_count, shell, marker_run) in expected.items():
        job = _mapping(jobs[job_id])
        assert "if" not in job
        steps = _steps(job)
        assert len(steps) == step_count

        marker = steps[0]
        assert set(marker) == {"name", "if", "shell", "working-directory", "run"}
        assert marker["name"] == _MARKER_NAME
        assert marker["if"] == _RELEASE_CONDITION
        assert marker["shell"] == shell
        assert marker["working-directory"] == "${{ github.workspace }}"
        assert marker["run"] == marker_run

        checkout_indexes = [index for index, step in enumerate(steps) if step.get("uses") == _CHECKOUT_ACTION]
        assert len(checkout_indexes) == 1
        assert checkout_indexes[0] > 0

        processing_steps = steps[1:]
        for step in processing_steps:
            step_name = step.get("name")
            expected_condition = _OWNER_CONDITION
            if job_id == "python-lint":
                if step_name == "Python 3.13 pytest":
                    expected_condition = _PYTHON_313_CONDITION
                elif step_name in {
                    "利用者設定から隔離したlockfile検証",
                    "Playwright Chromiumの導入",
                    "Python 3.14全検査",
                    "Python 3.14 E2E",
                }:
                    expected_condition = _PYTHON_314_CONDITION
            assert step.get("if") == expected_condition
            assert step_name != "statuslineの版数とタグを検査"

    rust_defaults = _mapping(_mapping(jobs["rust-lint"])["defaults"])
    assert _mapping(rust_defaults["run"])["working-directory"] == "rust/claude-statusline"
    assert sum(len(_steps(_mapping(jobs[job_id]))[1:]) for job_id in _COMMON_JOB_IDS) == 33


def test_common_job_display_names_keep_owner_and_non_owner_names(workflow_data: dict[str, object]) -> None:
    jobs = _jobs(workflow_data)
    expected_names = {
        "test-linux": ("${{ " + _RELEASE_CONDITION + " && 'test-linux (non-owner)' || 'test-linux' }}"),
        "test-windows": ("${{ " + _RELEASE_CONDITION + " && 'test-windows (non-owner)' || 'test-windows' }}"),
        "python-lint": (
            "${{ "
            + _RELEASE_CONDITION
            + " && format('python-lint ({0}) (non-owner)', matrix.python-version) "
            + "|| format('python-lint ({0})', matrix.python-version) }}"
        ),
        "rust-lint": ("${{ " + _RELEASE_CONDITION + " && 'rust-lint (non-owner)' || 'rust-lint' }}"),
    }

    for job_id, expected_name in expected_names.items():
        job = _mapping(jobs[job_id])
        assert job["name"] == expected_name

    python_job = _mapping(jobs["python-lint"])
    strategy = _mapping(python_job["strategy"])
    matrix = _mapping(strategy["matrix"])
    assert matrix["python-version"] == list(_PYTHON_VERSIONS)


def test_python_versions_have_distinct_pytest_full_and_e2e_routes(
    workflow_data: dict[str, object],
) -> None:
    jobs = _jobs(workflow_data)
    python_steps = {step.get("name"): step for step in _steps(_mapping(jobs["python-lint"]))}

    pytest_step = python_steps["Python 3.13 pytest"]
    full_step = python_steps["Python 3.14全検査"]
    e2e_step = python_steps["Python 3.14 E2E"]
    playwright_step = python_steps["Playwright Chromiumの導入"]
    lock_step = python_steps["利用者設定から隔離したlockfile検証"]

    assert pytest_step["if"] == _PYTHON_313_CONDITION
    assert pytest_step["run"] == "pyfltr ci --commands=pytest"
    assert "env" not in pytest_step

    assert full_step["if"] == _PYTHON_314_CONDITION
    assert full_step["run"] == "pyfltr ci --disable=claude-plugin-validate"
    assert _mapping(full_step["env"]) == {"SKIP": "cargo-fmt,cargo-clippy"}

    assert e2e_step["if"] == _PYTHON_314_CONDITION
    assert _mapping(e2e_step["env"]) == {"AGENT_TOOLKIT_SERVE_BROWSER_TESTS": "1"}
    assert e2e_step["run"] == (
        "uv run pytest agent-toolkit/scripts/_atk_serve_browser_test.py -o addopts='' -p no:cacheprovider"
    )
    assert playwright_step["if"] == e2e_step["if"]
    assert lock_step["if"] == _PYTHON_314_CONDITION
    assert pytest_step["if"] != full_step["if"]

    windows_steps = _steps(_mapping(jobs["test-windows"]))
    windows_test = next(step for step in windows_steps if step.get("name") == "managed-temp Windows境界テスト")
    assert "uv run --python 3.14 pytest -q" in typing.cast(str, windows_test["run"])


def test_statusline_version_is_an_independent_master_pull_request_check(
    workflow_data: dict[str, object],
) -> None:
    jobs = _jobs(workflow_data)
    statusline_job = _mapping(jobs["statusline-version"])
    assert statusline_job["name"] == "statusline-version"
    assert statusline_job["if"] == _STATUSLINE_CONDITION
    statusline_condition = statusline_job["if"]
    assert isinstance(statusline_condition, str)
    assert "head.repo" not in statusline_condition
    assert "head_ref" not in statusline_condition

    steps = _steps(statusline_job)
    assert len(steps) == 3
    assert steps[0]["uses"] == _CHECKOUT_ACTION
    assert _mapping(steps[0]["with"])["fetch-depth"] == "0"
    assert steps[1]["uses"] == _MISE_ACTION

    check_step = steps[2]
    assert check_step["name"] == "statuslineの版数とタグを検査"
    assert check_step["working-directory"] == "${{ github.workspace }}"
    assert _mapping(check_step["env"]) == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "CURRENT_SHA": "${{ github.sha }}",
    }
    assert "if" not in check_step

    rust_steps = _steps(_mapping(jobs["rust-lint"]))
    assert all(step.get("name") != "statuslineの版数とタグを検査" for step in rust_steps)
    assert statusline_job["name"] != _mapping(jobs["rust-lint"])["name"]


def test_job_and_step_cardinality(workflow_data: dict[str, object]) -> None:
    jobs = _jobs(workflow_data)
    assert list(jobs) == [*(_COMMON_JOB_IDS), "statusline-version"]
    assert len(jobs) == 5

    expanded_job_count = sum(2 if job_id == "python-lint" else 1 for job_id in jobs)
    assert expanded_job_count == 6

    definition_step_count = sum(len(_steps(_mapping(job))) for job in jobs.values())
    assert definition_step_count == 40

    expanded_step_count = sum(
        len(_steps(_mapping(job))) * (2 if job_id == "python-lint" else 1) for job_id, job in jobs.items()
    )
    assert expanded_step_count == 53

    owner_names = {
        "test-linux",
        "test-windows",
        "python-lint (3.13)",
        "python-lint (3.14)",
        "rust-lint",
    }
    non_owner_names = {f"{name} (non-owner)" for name in owner_names if not name.startswith("python-lint")}
    non_owner_names.update(f"python-lint ({version}) (non-owner)" for version in _PYTHON_VERSIONS)
    assert len(owner_names) == 5
    assert len(non_owner_names) == 5
    assert "rust-lint" not in non_owner_names
