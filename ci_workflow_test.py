"""CI workflowの静的契約を検証する。"""

import os
import shlex
import subprocess
import sys
import typing
from pathlib import Path

import pytest
import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parent
_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yaml"


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


def _statusline_job(workflow: dict[str, object]) -> dict[str, object]:
    jobs = [job for value in _jobs(workflow).values() if (job := _mapping(value)).get("name") == "statusline-version"]
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
    step = next(step for step in _steps(_statusline_job(workflow_data)) if step.get("name") == "statuslineの版数とタグを検査")
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


def test_direct_pytest_targets_exist(workflow_data: dict[str, object]) -> None:
    """workflowのpytestコマンドが直接指定するリポジトリ内の対象は実在する。"""
    targets = _direct_pytest_targets(workflow_data)
    assert targets
    for target in targets:
        assert (_REPOSITORY_ROOT / target).exists(), target
