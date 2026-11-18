"""CI workflowの主題別契約を検証する。"""

import ast
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from _test_helpers import (
    REPO_ROOT,
    direct_pytest_targets,
    expanded_workflow_steps,
    workflow_jobs,
    workflow_mapping,
    workflow_step_source,
    workflow_steps,
)
from _test_helpers import (
    _workflow_fixture as _workflow_data_fixture,  # noqa: F401  # pytestがfixtureとして収集する。
)


def _windows_step(steps: list[dict[str, object]], name: str) -> tuple[int, dict[str, object]]:
    matches = [(i, step) for i, step in enumerate(steps) if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


_MOZILLA_STEP = "Mozilla予約タスクの停止"


def test_windows_mozilla_tasks_are_stopped_before_upgrade_check(workflow_data: dict[str, object]) -> None:
    """更新検証の通常profile監視より前に、同じ実行条件でMozilla予約タスクを止める。"""
    steps = expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["update-dotfiles-upgrade-windows"]))
    stop_index, stop_step = _windows_step(steps, _MOZILLA_STEP)
    check_index, check_step = _windows_step(steps, "約3日前からの update-dotfiles 更新検証")
    assert stop_index < check_index
    assert stop_step.get("if") == check_step.get("if")
    assert stop_step.get("shell") == "pwsh"


def test_windows_job_runs_public_launcher_tests(workflow_data: dict[str, object]) -> None:
    """Windowsジョブが公開された起動スクリプトのテストを実行し、`.cmd`の分岐をWindowsで確かめる。"""
    _, step = _windows_step(
        expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows-tests"])),
        "公開入口ランチャーの動作確認",
    )
    targets = direct_pytest_targets({"jobs": {"test-windows-tests": {"steps": [step]}}})
    assert "bin/atk_launcher_test.py" in targets
    assert "bin/update_dotfiles_launcher_test.py" in targets


@pytest.mark.parametrize(
    "target",
    ["pytools/touch_file_test.py", "agent-toolkit/agent_toolkit/_common/plugin_environment_test.py"],
)
def test_windows_job_runs_link_and_prepare_lock_boundaries(workflow_data: dict[str, object], target: str) -> None:
    """リンク非追跡と共有環境のlock境界をWindows上で検証する指定を保つ。

    Windows境界テストは`windows` markerで対象を選ぶため、ステップがmarkerを選択し、対象ファイルがmarkerを持つことを確かめる。
    """
    _, step = _windows_step(
        expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows-tests"])), "Windows境界テスト"
    )
    tokens = shlex.split(str(step["run"]))
    assert tokens[tokens.index("-m") + 1] == "windows"
    assert "pytestmark = pytest.mark.windows" in (REPO_ROOT / target).read_text(encoding="utf-8").splitlines()


# Windowsで失敗していたクロスプラットフォーム試験。Windows境界テストが収集するよう`windows` markerを保つ。
_WINDOWS_CROSS_PLATFORM_TESTS = {
    "agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery_acquisition_commands_test.py": {
        "test_hook_chain_records_acquisition_without_blocking_write",
        "test_executed_static_read_records_acquisition_without_blocking_write",
        "test_named_skill_reference_and_invocation_keep_separate_evidence",
    },
    "agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery_acquisition_test.py": {
        "test_unchecked_condition_keeps_notice_suppressed_until_actual_resolution",
        "test_skill_acquisition_respects_model_invocation",
        "test_project_skill_cwd_boundary_keeps_acquisition_and_invocation_distinct",
        "test_role_finding_save_keeps_current_acquisition_evidence",
        "test_selected_shared_skill_keeps_current_role_boundary",
    },
    "agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery_compaction_test.py": {
        "test_public_hook_reuses_received_content_across_paths",
        "test_compaction_resets_acquisition_read_through_a_same_content_copy",
        "test_compaction_reguides_only_current_documents_without_blocking",
        "test_public_guidance_distinguishes_new_scope_target_and_compaction",
    },
    "agent-toolkit/agent_toolkit/_atk/wi/add_source_test.py": {
        "test_edit_accepts_minimal_agent_awi",
        "test_public_batch_preserves_source",
    },
    "agent-toolkit/agent_toolkit/_hooks/document_reads_test.py": {
        "test_receipt_candidates_share_document_scan_and_refresh_changed_body",
        "test_basics_reference_uses_observed_manifest_and_path",
        "test_chezmoi_source_and_distribution_link_require_full_body",
    },
    "pytools/_internal/update_claude_settings_test.py": {
        "test_placeholder_replaced_in_string",
        "test_placeholder_replaced_recursively_in_nested_structures",
    },
}


@pytest.mark.parametrize(("target", "functions"), sorted(_WINDOWS_CROSS_PLATFORM_TESTS.items()))
def test_windows_job_runs_cross_platform_tests(workflow_data: dict[str, object], target: str, functions: set[str]) -> None:
    """Windowsで失敗していた試験を、Windows境界テストの`-m windows`が収集・実行する指定を保つ。"""
    _, step = _windows_step(
        expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows-tests"])), "Windows境界テスト"
    )
    tokens = shlex.split(str(step["run"]))
    assert tokens[tokens.index("-m") + 1] == "windows"
    marked = {
        node.name
        for node in ast.walk(ast.parse((REPO_ROOT / target).read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and "pytest.mark.windows" in map(ast.unparse, node.decorator_list)
    }
    assert functions <= marked, sorted(functions - marked)


def test_windows_job_runs_pinned_codex_command_integration(workflow_data: dict[str, object]) -> None:
    """固定版CLIを導入した所有Windows jobが、探索の開始から通常コマンドへ到達することを検査する。"""
    job = workflow_mapping(workflow_jobs(workflow_data)["test-windows-tests"])
    assert job["runs-on"] == "windows-latest"
    steps = expanded_workflow_steps(job)
    install_index, install = _windows_step(steps, "Codex CLI 0.163.0の導入")
    integration_index, integration = _windows_step(steps, "Windows Codex command integration")
    assert install_index < integration_index
    assert install.get("if") == integration.get("if")
    script = workflow_step_source(install)
    assert "npm.cmd install --global --no-audit --no-fund @openai/codex@0.163.0" in script
    assert "codex-cli 0.163.0" in script
    assert "$env:GITHUB_PATH" in script
    targets = direct_pytest_targets({"jobs": {"test-windows-tests": {"steps": [integration]}}})
    prefix = "agent-toolkit/agent_toolkit/_agents_server/codex_sandbox_integration_test.py::"
    assert targets == [
        prefix + "test_explore_start_reaches_normal_commands",
        "agent-toolkit/agent_toolkit/_testing/resource_diagnostics_test.py",
    ]
    assert integration["working-directory"] == "${{ env.DOTFILES_DIR }}"
    integration_script = workflow_step_source(integration)
    assert "$env:ImageOS" in integration_script
    assert "$env:ImageVersion" in integration_script
    assert "codex --version" in integration_script
    assert "$pytestExitCode = $LASTEXITCODE" in integration_script
    assert "pytest_exit_code=$pytestExitCode" in integration_script
    assert "exit $pytestExitCode" in integration_script


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
@pytest.mark.parametrize("pytest_exit_code", [0, 7])
def test_windows_codex_integration_preserves_final_code(
    workflow_data: dict[str, object],
    tmp_path: Path,
    pytest_exit_code: int,
) -> None:
    """実PowerShellがpytest相当の成否を最終ログとstep終了コードの両方へ保持する。"""
    steps = expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows-tests"]))
    script = workflow_step_source(_windows_step(steps, "Windows Codex command integration")[1])
    probe = tmp_path / "integration.ps1"
    probe.write_text(
        "function codex { $global:LASTEXITCODE = 0 }\n"
        f"function uv {{ $global:LASTEXITCODE = {pytest_exit_code} }}\n" + script,
        encoding="utf-8",
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(probe)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == pytest_exit_code, result.stdout + result.stderr
    assert f"pytest_exit_code={pytest_exit_code}" in result.stdout
    assert "Codex境界pytest開始: at=" in result.stdout
    assert "Codex境界pytest終了: at=" in result.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh未インストール")
@pytest.mark.parametrize("failure", ["none", "npm", "missing-cli", "wrong-version"])
def test_windows_codex_installer_requires_pinned_executable(
    workflow_data: dict[str, object], tmp_path: Path, failure: str
) -> None:
    """導入stepの実PowerShellがnpm失敗・CLI不在・版不一致を非0にし、成功時だけPATHを配送する。"""
    steps = expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows-tests"]))
    script = workflow_step_source(_windows_step(steps, "Codex CLI 0.163.0の導入")[1])
    executable = tmp_path / "codex.ps1"
    version = "codex-cli 0.162.0" if failure == "wrong-version" else "codex-cli 0.163.0"
    executable.write_text(f"Write-Output '{version}'\n", encoding="utf-8-sig")
    quoted_cli = str(executable).replace("'", "''")
    quoted_directory = str(tmp_path).replace("'", "''")
    npm_status = 1 if failure == "npm" else 0
    binaries = (
        "@()"
        if failure == "missing-cli"
        else f"[pscustomobject]@{{FullName='{quoted_cli}';DirectoryName='{quoted_directory}'}}"
    )
    prelude = "\n".join(
        (
            f"function npm.cmd {{ $global:LASTEXITCODE = {npm_status}; Write-Output 'npm-test' }}",
            f"function Get-ChildItem {{ {binaries} }}",
        )
    )
    script_path = tmp_path / "install.ps1"
    script_path.write_text(f"{prelude}\n{script}", encoding="utf-8-sig")
    github_path = tmp_path / "github-path"
    pwsh = shutil.which("pwsh")
    assert pwsh is not None
    result = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        env={**os.environ, "GITHUB_PATH": str(github_path)},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert (result.returncode == 0) == (failure == "none"), result.stdout + result.stderr
    if failure == "none":
        assert github_path.read_text(encoding="utf-8-sig").strip() == str(tmp_path)
        assert version in result.stdout
    else:
        assert not github_path.exists()
    if failure in {"missing-cli", "wrong-version"}:
        assert "npm導入ログ" in result.stdout + result.stderr
        assert "Re-run failed" in result.stdout + result.stderr
        assert "再実行してください" in result.stdout + result.stderr


def test_windows_checks_run_as_independent_jobs(workflow_data: dict[str, object]) -> None:
    """Windowsの更新検証・境界テスト・初回導入は、互いに依存しない別jobがそれぞれ1回だけ所有する。"""
    jobs = workflow_jobs(workflow_data)
    owners = {
        name: {str(step.get("name")) for step in expanded_workflow_steps(workflow_mapping(job))}
        for name, job in jobs.items()
        if workflow_mapping(job)["runs-on"] == "windows-latest"
    }
    for step, owner in (
        ("約3日前からの update-dotfiles 更新検証", "update-dotfiles-upgrade-windows"),
        ("Windows境界テスト", "test-windows-tests"),
        ("公開入口ランチャーの動作確認", "test-windows-tests"),
        ("install.ps1から初回導入とバックアップを検証", "test-windows-install"),
    ):
        assert [name for name, names in owners.items() if step in names] == [owner], step
        assert workflow_mapping(jobs[owner])["needs"] == "common-ci-owner"


def test_windows_required_check_waits_for_tests_and_install(workflow_data: dict[str, object]) -> None:
    """必須checkの`test-windows`は境界テストと初回導入の両jobを待ち、両方の成功だけを成功にする。"""
    result = workflow_mapping(workflow_jobs(workflow_data)["test-windows-result"])
    assert result["needs"] == ["common-ci-owner", "test-windows-tests", "test-windows-install"]
    assert result["if"] == "${{ !cancelled() }}"
    step = workflow_steps(result)[-1]
    assert workflow_mapping(step["env"])["NEEDS"] == "${{ toJSON(needs) }}"
    assert step["run"] == "python3 scripts/ci_result.py"


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
    steps = expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["update-dotfiles-upgrade-windows"]))
    script = workflow_step_source(_windows_step(steps, _MOZILLA_STEP)[1])
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


def test_windows_install_checks_public_script_and_preserved_configuration(workflow_data: dict[str, object]) -> None:
    """候補版の公開導入を実行し、モード・復元用コピー・実適用結果を同じCIで確認する。"""
    steps = expanded_workflow_steps(workflow_mapping(workflow_jobs(workflow_data)["test-windows-install"]))
    installation = next(step for step in steps if step.get("name") == "install.ps1から初回導入とバックアップを検証")
    script = str(installation["run"])
    assert '& "$env:DOTFILES_DIR\\install.ps1" -Mode full' in script
    assert script.index("WriteAllText") < script.index("install.ps1") < script.index("$config =")
    # setup-uvのシム配置を通常のユーザー配置へ戻してから導入し、後処理まで到達させる。
    assert script.index("$env:UV_TOOL_BIN_DIR = Join-Path $env:USERPROFILE '.local\\bin'") < script.index("install.ps1")
    # 導入モードはWindowsのuser_config_dir（%LOCALAPPDATA%）から読む。
    assert "Join-Path $env:LOCALAPPDATA 'agent-toolkit\\config.json'" in script
    assert ".dotfiles-backup" in script and "-ceq $existing" in script
    assert "Get-FileHash $target" in script and "Get-FileHash $expected" in script
    assert not any(step.get("name") in {"chezmoi 適用", "dotfiles-post-apply の動作確認"} for step in steps)
    assert any(step.get("name") == "主要ファイルの存在確認" for step in steps)
