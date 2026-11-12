"""CI workflowの主題別契約を検証する。"""

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from _test_helpers import (
    _JUNIT_UPLOAD,
    _OWNS,
    REPO_ROOT,
    _run_git,
    _statusline_job,
    expanded_workflow_steps,
    workflow_jobs,
    workflow_mapping,
    workflow_steps,
)
from _test_helpers import (
    _workflow_fixture as _workflow_data_fixture,  # noqa: F401  # pytestがfixtureとして収集する。
)


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
        step
        for step in expanded_workflow_steps(_statusline_job(workflow_data))
        if step.get("name") == "statuslineの版数とタグを検査"
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


def test_plugin_version_job_compares_previous_published_commit(workflow_data: dict[str, object]) -> None:
    """developへのpushでは、push前の公開済みcommitと今回のcommitを版数据置き検査へ渡す。"""
    jobs = [
        job
        for value in workflow_jobs(workflow_data).values()
        if (job := workflow_mapping(value)).get("name") == "plugin-version"
    ]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/develop'"
    checkout = next(step for step in expanded_workflow_steps(job) if str(step.get("uses", "")).startswith("actions/checkout@"))
    assert str(workflow_mapping(checkout["with"])["fetch-depth"]) == "0"
    step = next(step for step in expanded_workflow_steps(job) if step.get("name") == "配布プラグインの版数据置きを検査")
    assert workflow_mapping(step["env"]) == {"BASE_SHA": "${{ github.event.before }}", "CURRENT_SHA": "${{ github.sha }}"}
    assert step["run"] == "python3 scripts/check_plugin_version.py"
    assert (REPO_ROOT / "scripts" / "check_plugin_version.py").is_file()


def test_python_jobs_separate_responsibilities(workflow_data: dict[str, object]) -> None:
    """同一検査の分割だけをmatrixにし、異なる責務は別jobで実行する。"""
    jobs = workflow_jobs(workflow_data)
    for name in ("python-lint", "pylint", "pytest-root"):
        assert "strategy" not in workflow_mapping(jobs[name])
    toolkit = workflow_mapping(jobs["pytest-toolkit"])
    assert workflow_mapping(toolkit["strategy"]) == {"fail-fast": "false", "matrix": {"shard": ["1/3", "2/3", "3/3"]}}
    root = next(
        step
        for step in expanded_workflow_steps(workflow_mapping(jobs["pytest-root"]))
        if step.get("name") == "Python 3.15 pytest 直下"
    )
    assert "':!:agent-toolkit/**' ':!:agent-toolkit-codex/**'" in str(root["run"])
    assert '--maxfail=0 "${targets[@]}"' in str(root["run"])
    test = next(step for step in expanded_workflow_steps(toolkit) if step.get("name") == "Python 3.15 pytest toolkit")
    assert "--test-shard=$TEST_SHARD" in str(test["run"])
    assert workflow_mapping(test["env"])["TEST_SHARD"] == "${{ matrix.shard }}"
    cached = []
    for name in ("python-lint", "pylint", "pytest-root", "pytest-toolkit"):
        prepare = next(
            step
            for step in workflow_steps(workflow_mapping(jobs[name]))
            if str(step.get("uses", "")).endswith("prepare-python-check")
        )
        if workflow_mapping(prepare["with"]).get("cache-mise") == "true":
            cached.append(name)
    assert cached == ["python-lint"]


@pytest.mark.parametrize(
    "job_name,dependencies", [("pytest-toolkit-result", ["pytest-toolkit"]), ("python-lint-result", ["python-lint", "pylint"])]
)
def test_required_results_use_needs(workflow_data: dict[str, object], job_name: str, dependencies: list[str]) -> None:
    """必須checkは検査の依存先を直接待ち、結果を集約する。"""
    job = workflow_mapping(workflow_jobs(workflow_data)[job_name])
    assert job["needs"] == ["common-ci-owner", *dependencies]
    assert job["if"] == "${{ !cancelled() }}"
    step = workflow_steps(job)[-1]
    assert workflow_mapping(step["env"])["NEEDS"] == "${{ toJSON(needs) }}"
    assert step["run"] == "python3 scripts/ci_result.py"


def test_required_check_display_names(workflow_data: dict[str, object]) -> None:
    """rulesetが参照する9表示名と、共通checkの非所有名を維持する。"""
    jobs = workflow_jobs(workflow_data)
    for key, display in {
        "test-linux": "test-linux",
        "test-windows": "test-windows",
        "python-lint-result": "python-lint (3.15)",
        "pytest-root": "pytest (3.15)",
        "pytest-toolkit-result": "pytest-toolkit (3.15)",
        "rust-lint": "rust-lint",
        "browser-e2e": "browser-e2e",
        "update-dotfiles-upgrade-windows": "update-dotfiles-upgrade (windows)",
    }.items():
        assert workflow_mapping(jobs[key])["name"] == (
            f"${{{{ needs.common-ci-owner.outputs.owns == 'false' && '{display} (non-owner)' || '{display}'" + " }}"
        )
    assert workflow_mapping(jobs["statusline-version"])["name"] == "statusline-version"


def _makefile_variable(name: str) -> str:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)} := (.+)$", text, flags=re.MULTILINE)
    assert match is not None, name
    return match.group(1).strip()


def test_ci_deps_check_matches_ci_container(workflow_data: dict[str, object]) -> None:
    """`make ci-deps-check`がCIのpython-lint jobと同じimage・導入パッケージ・Python版で依存導入を再現する。

    CI定義だけを変えた場合も、Makefileの値との不一致でpush前の再現手段のずれを検出する。
    """
    job = workflow_mapping(workflow_jobs(workflow_data)["python-lint"])
    container = workflow_mapping(job["container"])
    assert _makefile_variable("CI_CONTAINER_IMAGE") == container["image"]

    install = next(step for step in expanded_workflow_steps(job) if step.get("name") == "ネイティブ依存インストール")
    install_lines = [line.strip() for line in str(install["run"]).splitlines() if "apt-get install" in line]
    assert len(install_lines) == 1
    packages = install_lines[0].split("--no-install-recommends", 1)[1].split()
    assert _makefile_variable("CI_NATIVE_PACKAGES").split() == packages

    versions = {
        str(workflow_mapping(workflow_mapping(value)["env"])["UV_PYTHON"])
        for name, value in workflow_jobs(workflow_data).items()
        if name in {"python-lint", "pylint", "pytest-root", "pytest-toolkit"}
    }
    assert set(_makefile_variable("CI_PYTHON_VERSIONS").split()) == versions


def test_update_checks_container_dependencies_after_lock_updates() -> None:
    """依存更新は両lockの更新後にcontainerの依存導入を確かめ、全体試験を呼ばない。"""
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^update:\n((?:\t.*\n)+)", text, flags=re.MULTILINE)
    assert match is not None
    commands = match.group(1).splitlines()
    check = commands.index("\t$(MAKE) ci-deps-check")
    assert next(index for index, command in enumerate(commands) if "uv sync --upgrade" in command) < check
    assert (
        next(index for index, command in enumerate(commands) if "uv lock --upgrade --project agent-toolkit" in command) < check
    )
    assert "\t$(MAKE) test" not in commands


@pytest.mark.parametrize("scope", ["直下", "toolkit"])
def test_minimum_git_is_passed_to_normal_pytest(workflow_data: dict[str, object], scope: str) -> None:
    """両pytest要素が、READMEの対応版のGitを通常pytestへ渡して各1回実行する。"""
    job = workflow_mapping(workflow_jobs(workflow_data)["pytest-root" if scope == "直下" else "pytest-toolkit"])
    steps = expanded_workflow_steps(job)
    names = [step.get("name") for step in steps]
    pytest_name = f"Python 3.15 pytest {scope}"
    pytest_index = names.index(pytest_name)
    install_index = names.index("Git 2.34.1の導入")
    assert install_index < pytest_index
    assert names.count(pytest_name) == 1
    assert "Git 2.34.1での実Gitテスト" not in names
    install, pytest_step = steps[install_index], steps[pytest_index]
    assert str(pytest_step["if"]).startswith(_OWNS)

    package = str(workflow_mapping(install["env"])["GIT_DEB"])
    match = re.fullmatch(r"git_(\d+\.\d+\.\d+)-[0-9A-Za-z.]+_amd64\.deb", package)
    assert match is not None, package
    version = match.group(1)
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"agent-toolkitのGit呼び出しはGit {version}以上に対応する。" in readme
    assert f'grep -Fx "git version {version}"' in str(install["run"])
    assert "sha256sum -c" in str(install["run"])

    environment = workflow_mapping(pytest_step["env"])
    assert environment["AGENT_TOOLKIT_TEST_GIT"] == f"${{{{ steps.{install['id']}.outputs.git }}}}"
    assert "--maxfail=0" in str(pytest_step["run"])

    # 全ケースの合否と個別所要を、pytestの成否によらず各jobの同じ結果としてartifactへ保存する。
    command = str(pytest_step["run"])
    assert command.startswith("set -euo pipefail\n")
    assert 'echo "junit=$junit"' in command and '>> "$GITHUB_OUTPUT"' in command
    assert command.index('echo "junit=$junit"') < command.index("--junitxml=")
    assert ('--junitxml="$junit"' if scope == "直下" else "--junitxml=$junit ") in command
    assert pytest_step["id"] == "pytest"
    upload = steps[pytest_index + 1]
    assert str(upload["uses"]).startswith("actions/upload-artifact@")
    assert upload["if"] == _JUNIT_UPLOAD
    assert "continue-on-error" not in pytest_step and "continue-on-error" not in upload
    settings = workflow_mapping(upload["with"])
    assert settings["path"] == "${{ steps.pytest.outputs.junit }}"
    assert settings["if-no-files-found"] == "error"
    if scope == "直下":
        assert 'junit="$RUNNER_TEMP/pytest-root-junit.xml"' in command
        assert settings["name"] == "pytest-root-junit"
    else:
        # artifact名は分割ごとに一意にし、名前に使えない`/`を分割指定から除く。
        assert 'shard="${TEST_SHARD%%/*}-of-${TEST_SHARD##*/}"' in command
        assert 'junit="$RUNNER_TEMP/pytest-toolkit-junit-$shard.xml"' in command
        assert 'echo "artifact=pytest-toolkit-junit-$shard"' in command
        assert settings["name"] == "${{ steps.pytest.outputs.artifact }}"


def test_linux_job_places_temporary_files_on_runner_disk(workflow_data: dict[str, object]) -> None:
    """test-linuxは大容量の一時物を生成する全stepより前に、job全体の一時領域をrunnerの作業ディスクへ向ける。

    Ubuntu 26.04の/tmpはquota付きtmpfsであり、完全導入の検証homeや展開物が容量制限で失敗する。
    """
    steps = expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-linux"]))
    runs = [str(step.get("run", "")) for step in steps]
    placement = runs.index('echo "TMPDIR=$RUNNER_TEMP" >> "$GITHUB_ENV"')
    for marker in ("scripts/check_update_dotfiles_upgrade.py", "install.sh"):
        assert placement < next(index for index, run in enumerate(runs) if marker in run), marker


def test_removal_registry_expiry_runs_only_in_scheduled_audit() -> None:
    """撤去表の期限は定期実行のAuditだけで判定し、push・PR毎のCIでは判定しない。

    期限の判定は実行日で結果が変わるため、無関係な変更のCIとcommit hookを日付で止めないようにする。
    """
    audit = yaml.safe_load((REPO_ROOT / ".github/workflows/audit.yaml").read_text(encoding="utf-8"))
    command = "uv run --frozen python -m pytools._internal.removal_registry_report"
    steps = audit["jobs"]["removal-registry"]["steps"]
    assert any(step.get("run") == command for step in steps)
    assert set(audit[True]) == {"schedule", "workflow_dispatch"}
    ci_text = (REPO_ROOT / ".github/workflows/ci.yaml").read_text(encoding="utf-8")
    assert "removal_registry_report" not in ci_text


@pytest.mark.skipif(os.name == "nt", reason="Linux Audit jobのbashで検証する")
@pytest.mark.parametrize(
    ("report", "audit_rc", "expected_rc", "message"),
    [
        ('{"vulnerabilities":{"found":false,"count":0,"list":[]}}', 0, 0, "Rust監査完了"),
        ('{"vulnerabilities":{"found":true,"count":1,"list":[]}}', 1, 1, "既知の脆弱性があります"),
        ('{"vulnerabilities":{"found":false,"count":0,"list":[]}}', 2, 2, "異常終了"),
        ("", 2, 1, "結果を取得できません"),
    ],
)
def test_rust_audit_distinguishes_findings_and_tool_failure(
    tmp_path: Path, host_environ: Callable[[], dict[str, str]], report: str, audit_rc: int, expected_rc: int, message: str
) -> None:
    """実workflowのシェルでRustSec結果とツール異常を区別し、検出を正常へ変換しない。"""
    audit = yaml.safe_load((REPO_ROOT / ".github/workflows/audit.yaml").read_text(encoding="utf-8"))
    # cargo-auditの導入はCリンカーを要するため、Python監査のpyfltrコンテナーとは別のホストrunnerで実行する。
    rust_job = audit["jobs"]["rust-audit"]
    assert "container" not in rust_job
    steps = rust_job["steps"]
    names = [step.get("name") or step.get("uses", "") for step in steps]
    install_index = names.index("Rust監査ツールの導入")
    assert any(name.startswith("jdx/mise-action@") for name in names[:install_index])
    assert "mise exec -- cargo install --locked --no-default-features cargo-audit" in steps[install_index]["run"]
    script = next(step["run"] for step in steps if step.get("name") == "Rust依存の監査")
    assert "cargo audit --file rust/claude-statusline/Cargo.lock --json" in script
    assert any(step.get("name") == "Run dependency audit" for step in audit["jobs"]["audit"]["steps"])
    binary = tmp_path / "bin"
    binary.mkdir()
    mise = binary / "mise"
    mise.write_text('#!/bin/sh\nprintf "%s" "$AUDIT_REPORT"\nexit "$AUDIT_RC"\n', encoding="utf-8")
    mise.chmod(0o755)
    source = tmp_path / "audit.sh"
    source.write_text(script, encoding="utf-8")
    environment = host_environ()
    environment.update(
        PATH=str(binary) + os.pathsep + environment["PATH"],
        RUNNER_TEMP=str(tmp_path),
        AUDIT_REPORT=report,
        AUDIT_RC=str(audit_rc),
    )
    result = subprocess.run(
        ["bash", str(source)], env=environment, capture_output=True, text=True, encoding="utf-8", check=False, timeout=10
    )
    assert result.returncode == expected_rc, result.stdout + result.stderr
    assert message in result.stdout
    if report:
        assert report in result.stdout
