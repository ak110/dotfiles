"""CI workflowの静的契約を検証する。"""

import os
import shutil
import subprocess
import sys
import typing
from pathlib import Path

import pytest

from _test_helpers import (
    REPO_ROOT,
    WORKFLOW_PATH,
    direct_pytest_targets,
    load_workflow,
    workflow_jobs,
    workflow_mapping,
    workflow_steps,
)


def _statusline_job(workflow: dict[str, object]) -> dict[str, object]:
    jobs = [
        job
        for value in workflow_jobs(workflow).values()
        if (job := workflow_mapping(value)).get("name") == "statusline-version"
    ]
    assert len(jobs) == 1
    return jobs[0]


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _create_statusline_repository(tmp_path: Path, *, statusline_changed: bool) -> tuple[Path, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _run_git(origin, "init", "--initial-branch=master")
    _run_git(origin, "config", "user.email", "ci@example.com")
    _run_git(origin, "config", "user.name", "CI")

    manifest = origin / "rust" / "claude-statusline" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('[package]\nname = "claude-statusline"\nversion = "1.0.0"\n', encoding="utf-8")
    (origin / "unrelated.txt").write_text("master\n", encoding="utf-8")
    _run_git(origin, "add", ".")
    _run_git(origin, "commit", "-m", "initial")
    _run_git(origin, "switch", "-c", "develop")

    target = manifest if statusline_changed else origin / "unrelated.txt"
    addition = "# develop\n" if statusline_changed else "develop\n"
    target.write_text(target.read_text(encoding="utf-8") + addition, encoding="utf-8")
    _run_git(origin, "add", ".")
    _run_git(origin, "commit", "-m", "develop change")

    checkout = tmp_path / "checkout"
    _run_git(tmp_path, "clone", "--branch", "develop", origin.as_uri(), str(checkout))
    return checkout, _run_git(checkout, "rev-parse", "HEAD")


@pytest.fixture(scope="module", name="workflow_data")
def _workflow_fixture() -> dict[str, object]:
    return load_workflow()


@pytest.mark.parametrize(
    ("statusline_changed", "expected_returncode", "expected_output"),
    [
        (False, 0, "statuslineの変更がないため、版数検査を省略する。"),
        (True, 1, "statuslineの変更にはCargo.tomlの版数更新が必要である。"),
    ],
)
def test_statusline_version_develop_push_reaches_version_check(
    workflow_data: dict[str, object],
    tmp_path: Path,
    *,
    statusline_changed: bool,
    expected_returncode: int,
    expected_output: str,
) -> None:
    """developへのpushではmasterとの共通祖先を解決し、statuslineの変更有無を検査する。"""
    checkout, current_sha = _create_statusline_repository(tmp_path, statusline_changed=statusline_changed)
    # stepはリポジトリ直下の判定スクリプトを相対パスで呼ぶため、検査対象の複製へ同じ位置で置く。
    scripts = checkout / "scripts"
    scripts.mkdir()
    (scripts / "check_statusline_version.py").write_bytes((REPO_ROOT / "scripts" / "check_statusline_version.py").read_bytes())
    step = next(
        step for step in workflow_steps(_statusline_job(workflow_data)) if step.get("name") == "statuslineの版数とタグを検査"
    )
    script = step["run"]
    assert isinstance(script, str)

    result = subprocess.run(
        ["bash"],
        cwd=checkout,
        env={
            "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
            "BASE_SHA": "",
            "CURRENT_SHA": current_sha,
        },
        input=script,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_returncode
    assert expected_output in result.stdout + result.stderr


def _windows_step(steps: list[dict[str, object]], name: str) -> tuple[int, dict[str, object]]:
    matches = [(i, step) for i, step in enumerate(steps) if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


_MOZILLA_STEP = "Mozilla予約タスクの停止"


def test_windows_mozilla_tasks_are_stopped_before_upgrade_check(workflow_data: dict[str, object]) -> None:
    """更新検証の通常profile監視より前に、同じ実行条件でMozilla予約タスクを止める。"""
    steps = workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["update-dotfiles-upgrade-windows"]))
    stop_index, stop_step = _windows_step(steps, _MOZILLA_STEP)
    check_index, check_step = _windows_step(steps, "約3日前からの update-dotfiles 更新検証")
    assert stop_index < check_index
    assert stop_step.get("if") == check_step.get("if")
    assert stop_step.get("shell") == "pwsh"


def test_windows_job_runs_public_launcher_tests(workflow_data: dict[str, object]) -> None:
    """Windowsジョブが公開入口ランチャーのテストを実行し、`.cmd`の分岐をWindowsで確かめる。"""
    _, step = _windows_step(
        workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows"])), "公開入口ランチャーの動作確認"
    )
    targets = direct_pytest_targets({"jobs": {"test-windows": {"steps": [step]}}})
    assert "bin/atk_launcher_test.py" in targets
    assert "bin/update_dotfiles_launcher_test.py" in targets


def test_windows_upgrade_is_independent_from_windows_tests(workflow_data: dict[str, object]) -> None:
    """更新検証とWindows固有検査は依存関係のない別jobが所有する。"""
    jobs = workflow_jobs(workflow_data)
    upgrade = workflow_mapping(jobs["update-dotfiles-upgrade-windows"])
    windows = workflow_mapping(jobs["test-windows"])
    assert "needs" not in upgrade
    assert "needs" not in windows
    assert all(step.get("name") != "約3日前からの update-dotfiles 更新検証" for step in workflow_steps(windows))
    assert any(step.get("name") == "公開入口ランチャーの動作確認" for step in workflow_steps(windows))


def test_python_315_pytest_is_an_independent_matrix_job(workflow_data: dict[str, object]) -> None:
    """最新基準版のlintとpytestを分離し、下限版と前版のpytestを維持する。"""
    job = workflow_mapping(workflow_jobs(workflow_data)["python-lint"])
    strategy = workflow_mapping(job["strategy"])
    matrix = workflow_mapping(strategy["matrix"])
    include = [workflow_mapping(value) for value in typing.cast(list[object], matrix["include"])]
    assert include == [
        {"python-version": "3.13", "check": "pytest", "check-name": "python-lint (3.13)"},
        {"python-version": "3.14", "check": "pytest", "check-name": "pytest (3.14)"},
        {"python-version": "3.15", "check": "lint", "check-name": "python-lint (3.15)"},
        {"python-version": "3.15", "check": "pytest", "check-name": "pytest (3.15)"},
    ]
    steps = workflow_steps(job)
    lint = next(step for step in steps if step.get("name") == "Python 3.15 pytest以外の検査")
    pytest_step = next(step for step in steps if step.get("name") == "Python 3.15 pytest")
    assert lint["run"] == "pyfltr ci --disable=pytest,claude-plugin-validate,statusline-version"
    assert pytest_step["run"] == "pyfltr ci --commands=pytest"
    cache = next(step for step in steps if str(step.get("uses", "")).startswith("actions/cache@"))
    cache_with = workflow_mapping(cache["with"])
    assert "${{ matrix.python-version }}" in str(cache_with["key"])
    assert "${{ matrix.check }}" in str(cache_with["key"])


_RELEASE_PULL_REQUEST_CONDITIONS = (
    "github.event_name == 'pull_request'",
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.head_ref == 'develop'",
    "github.base_ref == 'master'",
)
_OWNS = "steps.owner.outputs.owns == 'true'"


def _common_job_names(workflow: dict[str, object]) -> list[str]:
    """release pull requestで非所有の表示名を持つ共通jobの名前を返す。"""
    return [name for name, job in workflow_jobs(workflow).items() if "(non-owner)" in str(workflow_mapping(job)["name"])]


def test_common_jobs_compute_release_pull_request_once(workflow_data: dict[str, object]) -> None:
    """全共通jobが先頭の判定stepで1回だけ判定し、非所有markerと後続stepは判定式を書かずにその出力を参照する。"""
    common_jobs = _common_job_names(workflow_data)
    assert common_jobs == [
        "test-linux",
        "update-dotfiles-upgrade-windows",
        "test-windows",
        "python-lint",
        "browser-e2e",
        "rust-lint",
    ]
    for job_name in common_jobs:
        steps = workflow_steps(workflow_mapping(workflow_jobs(workflow_data)[job_name]))
        judgment, marker, *real_steps = steps
        assert judgment["id"] == "owner", job_name
        assert "if" not in judgment, job_name
        release_expression = str(workflow_mapping(judgment["env"])["RELEASE_PULL_REQUEST"])
        assert all(condition in release_expression for condition in _RELEASE_PULL_REQUEST_CONDITIONS), job_name
        assert marker["name"] == "共通CI非所有経路", job_name
        assert marker["if"] == "steps.owner.outputs.owns == 'false'", job_name
        for step in [marker, *real_steps]:
            condition = str(step.get("if", ""))
            assert not any(term in condition for term in ("github.head_ref", "github.base_ref", "github.event_name")), (
                job_name,
                step.get("name"),
            )
        assert all(str(step.get("if", "")).startswith(_OWNS) for step in real_steps), job_name


def test_chezmoi_is_installed_by_shared_action(workflow_data: dict[str, object]) -> None:
    """chezmoiを導入するjobは、導入の処理を写さずリポジトリのcomposite actionを使う。"""
    installers = {
        job_name: step
        for job_name, job in workflow_jobs(workflow_data).items()
        for step in workflow_steps(workflow_mapping(job))
        if step.get("name") == "chezmoi インストール"
    }
    assert sorted(installers) == ["python-lint", "test-linux", "test-windows", "update-dotfiles-upgrade-windows"]
    for job_name, step in installers.items():
        assert str(step["uses"]).endswith(".github/actions/install-chezmoi"), job_name
        assert "run" not in step, job_name
    assert "install_chezmoi()" not in WORKFLOW_PATH.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
@pytest.mark.parametrize(
    ("tasks", "expected_lines", "unexpected"),
    [
        (
            [("\\Mozilla\\", "Firefox Default Browser Agent 308046B0AF4A39CB", "Running"), ("\\Other\\", "Keep", "Ready")],
            [
                "STOP \\Mozilla\\Firefox Default Browser Agent 308046B0AF4A39CB",
                "TaskPath=\\Mozilla\\ TaskName=Firefox Default Browser Agent 308046B0AF4A39CB State=Disabled",
            ],
            "Keep",
        ),
        ([("\\Other\\", "Keep", "Ready")], ["TaskPathが\\Mozilla\\の予約タスクは0件である。"], "Keep"),
    ],
)
def test_windows_mozilla_step_disables_only_mozilla_tasks(
    workflow_data: dict[str, object],
    tmp_path: Path,
    tasks: list[tuple[str, str, str]],
    expected_lines: list[str],
    unexpected: str,
) -> None:
    """ステップ本体は`\\Mozilla\\`配下だけを停止・無効化して結果を出力し、0件でも成功する。"""
    steps = workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["update-dotfiles-upgrade-windows"]))
    script = _windows_step(steps, _MOZILLA_STEP)[1]["run"]
    assert isinstance(script, str)
    # Windowsの予約タスクcmdletは他のOSに無いため、同名の関数で置き換えて分岐と出力を確かめる。
    entries = ",".join(
        f"[pscustomobject]@{{TaskPath='{path}';TaskName='{name}';State='{state}'}}" for path, name, state in tasks
    )
    prelude = "\n".join(
        (
            f"function Get-ScheduledTask {{ @({entries}) }}",
            'function Stop-ScheduledTask { param($TaskPath, $TaskName) Write-Output "STOP $TaskPath$TaskName" }',
            "function Disable-ScheduledTask { param($TaskPath, $TaskName) "
            "[pscustomobject]@{TaskPath=$TaskPath;TaskName=$TaskName;State='Disabled'} }",
        )
    )
    script_path = tmp_path / "step.ps1"
    script_path.write_text(f"{prelude}\n{script}", encoding="utf-8-sig")
    pwsh = shutil.which("pwsh")
    assert pwsh is not None

    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    for line in expected_lines:
        assert line in lines
    assert unexpected not in result.stdout
