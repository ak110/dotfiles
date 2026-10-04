"""CI workflowの静的契約を検証する。"""

import os
import shlex
import shutil
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
    # stepはリポジトリ直下の判定スクリプトを相対パスで呼ぶため、検査対象の複製へ同じ位置で置く。
    scripts = checkout / "scripts"
    scripts.mkdir()
    (scripts / "check_statusline_version.py").write_bytes(
        (_REPOSITORY_ROOT / "scripts" / "check_statusline_version.py").read_bytes()
    )
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


def _flag_options(tokens: list[str]) -> set[str]:
    """値を取る`--project`を除いた`--`始まりのオプションを返す。"""
    return {token for token in tokens if token.startswith("--") and token != "--project"}


def _windows_step(steps: list[dict[str, object]], name: str) -> tuple[int, dict[str, object]]:
    matches = [(i, step) for i, step in enumerate(steps) if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


_MOZILLA_STEP = "Mozilla予約タスクの停止"


def test_windows_mozilla_tasks_are_stopped_before_upgrade_check(workflow_data: dict[str, object]) -> None:
    """更新検証の通常profile監視より前に、同じ実行条件でMozilla予約タスクを止める。"""
    steps = _steps(_mapping(_jobs(workflow_data)["test-windows"]))
    stop_index, stop_step = _windows_step(steps, _MOZILLA_STEP)
    check_index, check_step = _windows_step(steps, "約3日前からの update-dotfiles 更新検証")
    assert stop_index < check_index
    assert stop_step.get("if") == check_step.get("if")
    assert stop_step.get("shell") == "pwsh"


def test_windows_job_runs_public_launcher_tests(workflow_data: dict[str, object]) -> None:
    """Windowsジョブが公開入口ランチャーのテストを実行し、`.cmd`の分岐をWindowsで確かめる。"""
    _, step = _windows_step(_steps(_mapping(_jobs(workflow_data)["test-windows"])), "公開入口ランチャーの動作確認")
    targets = _direct_pytest_targets({"jobs": {"test-windows": {"steps": [step]}}})
    assert "bin/atk_launcher_test.py" in targets
    assert "bin/update_dotfiles_launcher_test.py" in targets


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
    steps = _steps(_mapping(_jobs(workflow_data)["test-windows"]))
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
