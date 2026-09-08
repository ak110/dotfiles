"""post-applyテンプレートの最終実行順序を検証する。"""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
LINUX_TEMPLATE = REPO_ROOT / ".chezmoi-source/run_after_post-apply.sh.tmpl"
WINDOWS_TEMPLATE = REPO_ROOT / ".chezmoi-source/run_after_post-apply-windows.ps1.tmpl"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


def _read(path: Path) -> str:
    """BOMの有無を許容してテンプレートを読む。"""
    return path.read_text(encoding="utf-8-sig")


def test_linux_post_apply_is_final_operation() -> None:
    """Linux成功分岐はpost-applyをexecし、後続出力を持たない。"""
    text = _read(LINUX_TEMPLATE)
    final_block = text[text.rindex('post_apply_bin="$HOME/.local/bin/dotfiles-post-apply"') :].strip()

    assert 'exec "$post_apply_bin"' in final_block
    assert final_block.splitlines()[-4:] == [
        '    exec "$post_apply_bin"',
        "else",
        '    echo "  [pytools] $post_apply_bin が見つからないため後処理スキップ"',
        "fi",
    ]


def test_windows_post_apply_final_condition_has_branch_local_order() -> None:
    """Windowsの復帰処理後にpost-applyの最終条件分岐だけを置く。"""
    text = _read(WINDOWS_TEMPLATE)
    final_start = text.rindex("$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'")
    before = text[:final_start]
    final_block = text[final_start:].strip()

    assert "Start-Process" in before
    assert "Start-Process" not in final_block
    assert final_block.splitlines() == [
        "$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'",
        "if (Test-Path $postApplyBin) {",
        "    # dotfiles-post-apply は chezmoi から独立した CLI のため、workingTree を環境変数で渡す",
        "    $env:CHEZMOI_WORKING_TREE = '{{ .chezmoi.workingTree }}'",
        "    & $postApplyBin",
        "} else {",
        '    Write-Host "  [pytools] $postApplyBin が見つからないため後処理スキップ"',
        "}",
    ]


def test_windows_media_remote_has_pre_post_apply_fallback() -> None:
    """post-apply前のmedia-remote復帰に配布バイナリの代替経路を持つ。"""
    text = _read(WINDOWS_TEMPLATE)
    final_start = text.rindex("$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'")
    before = text[:final_start]

    assert "$mediaRemoteBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-media-remote.exe'" in before
    assert before.count("elseif (Test-Path $mediaRemoteBin)") == 2
    assert before.count("Start-Process -FilePath $mediaRemoteBin -WindowStyle Hidden -ArgumentList @('serve')") == 2
    assert before.count("Start-Process -FilePath 'wscript.exe'") == 2
    assert before.index("Start-Process -FilePath 'wscript.exe'") < before.index("elseif (Test-Path $mediaRemoteBin)")


def test_windows_reinstall_defers_without_stopping_unrestorable_processes() -> None:
    """復元不能なlocking processがある場合は停止と再導入を行わず、後続処理を継続する。"""
    text = _read(WINDOWS_TEMPLATE)

    classification = text.index("$unrestorableProcs += $p")
    guard = text.index("if ($unrestorableProcs.Count -gt 0)")
    stop_loop = text.index("foreach ($p in $restorableProcs)")
    stop_process = text.index("Stop-Process -Id $p.ProcessId", stop_loop)
    install_guard = text.index("if ($needsReinstall -and -not $reinstallDeferred)")
    install = text.index('& uv tool install --editable "{{ .chezmoi.workingTree }}"')
    deferred = text.index("} elseif ($reinstallDeferred) {")

    assert classification < guard < stop_loop < stop_process < install_guard < install < deferred
    assert "$reinstallDeferred = $true" in text[guard:stop_loop]
    assert "$env:DOTFILES_PYTOOLS_INSTALL_STATE = 'deferred'" in text[guard:stop_loop]
    assert "$env:DOTFILES_PYTOOLS_INSTALL_DETAIL" in text[guard:stop_loop]
    assert "Stop-Process" not in text[guard:stop_loop]
    assert "再インストールを次回へ延期" in text[guard:stop_loop]
    assert "既存版で後続処理を継続" in text[deferred:]
    assert text.count("Get-CimInstance Win32_Process") == 1
    assert text.count("Get-PytoolsEnvLockingProcess") == 3


def test_windows_install_failure_records_state_and_detail() -> None:
    """Windows側の導入失敗を例外メッセージ付きでpost-applyへ渡す。"""
    text = _read(WINDOWS_TEMPLATE)
    catch = text.index("    } catch {")
    failure = text.index("インストールに失敗しました", catch)

    assert "$env:DOTFILES_PYTOOLS_INSTALL_STATE = 'failed'" in text[catch:failure]
    assert "$env:DOTFILES_PYTOOLS_INSTALL_DETAIL = $_.Exception.Message" in text[catch:failure]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell実行体が見つからない")
def test_windows_locking_processes_classify_and_record_restart_state(
    tmp_path: Path,
) -> None:
    """配布exeとmoduleを分類し、停止前の再起動対象を正しく記録する。"""
    powershell = POWERSHELL
    assert powershell is not None
    text = _read(WINDOWS_TEMPLATE)
    classifier_start = text.index("$escapedMediaRemoteBin =")
    classifier_end = text.index("\n# uv tool venv", classifier_start)
    classifier = text[classifier_start:classifier_end]
    start = text.index("if ($needsReinstall) {")
    end = text.index("\n\n# post-apply 配下の出力", start)
    process_handling = text[start:end]

    def run_scenario(
        name: str,
        fixtures: str,
        setup: str,
        body: str,
        result_expression: str,
    ) -> tuple[dict[str, object], list[str]]:
        script = tmp_path / f"{name}.ps1"
        script.write_text(
            """Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
"""
            + setup
            + """
$stopped = @()
$getCimCalls = 0
$fixtures = @(
"""
            + fixtures
            + """)
function Get-CimInstance {
    [CmdletBinding()]
    param([string] $ClassName)
    $script:getCimCalls += 1
    return $fixtures
}
function Stop-Process {
    [CmdletBinding()]
    param([int] $Id, [switch] $Force)
    $script:stopped += $Id
}
function Start-Sleep {
    [CmdletBinding()]
    param([int] $Milliseconds)
}
"""
            + classifier
            + "\n"
            + body
            + "\n"
            + result_expression,
            encoding="utf-8-sig",
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr or completed.stdout
        output = completed.stdout.splitlines()
        return json.loads(output[-1]), output[:-1]

    def run_reinstall_scenario(name: str, fixtures: str) -> tuple[dict[str, object], list[str]]:
        return run_scenario(
            name,
            fixtures,
            """$needsReinstall = $true
$mediaRemoteWasRunning = $false
$reinstallDeferred = $false
$uvToolDir = 'C:\\tools\\pytools'
$mediaRemoteBin = 'C:\\Users\\test\\.local\\bin\\dotfiles-media-remote.exe'
$env:DOTFILES_PYTOOLS_INSTALL_STATE = ''
$env:DOTFILES_PYTOOLS_INSTALL_DETAIL = ''
""",
            process_handling,
            """[pscustomobject]@{
    Locking = @($lockingProcs | ForEach-Object { $_.ProcessId })
    Restorable = @($restorableProcs | ForEach-Object { $_.ProcessId })
    Unrestorable = @($unrestorableProcs | ForEach-Object { $_.ProcessId })
    Stopped = @($stopped)
    MediaRemoteWasRunning = $mediaRemoteWasRunning
    ReinstallDeferred = $reinstallDeferred
    GetCimCalls = $getCimCalls
} | ConvertTo-Json -Compress
""",
        )

    distributed_fixture = """    [pscustomobject]@{
        ProcessId = 1
        Name = 'DOTFILES-MEDIA-REMOTE.EXE'
        ExecutablePath = 'C:\\USERS\\TEST\\.LOCAL\\BIN\\DOTFILES-MEDIA-REMOTE.EXE'
        CommandLine = 'dotfiles-media-remote.exe serve'
    }
"""
    distributed_exe, distributed_output = run_reinstall_scenario("distributed-media-remote", distributed_fixture)
    assert distributed_exe == {
        "Locking": [1],
        "Restorable": [1],
        "Unrestorable": [],
        "Stopped": [1],
        "MediaRemoteWasRunning": True,
        "ReinstallDeferred": False,
        "GetCimCalls": 1,
    }
    assert "  [pytools] 更新対象の使用中プロセス: 候補1件 再起動可1件 再起動不可0件" in distributed_output

    mixed_fixture = """    [pscustomobject]@{
        ProcessId = 2
        Name = 'pythonw.exe'
        ExecutablePath = 'C:\\tools\\pytools\\Scripts\\pythonw.exe'
        CommandLine = 'pythonw.exe C:\\Users\\test\\.local\\bin\\dotfiles-media-remote.exe serve'
    },
    [pscustomobject]@{
        ProcessId = 3
        Name = 'pythonw.exe'
        ExecutablePath = 'C:\\tools\\pytools\\Scripts\\pythonw.exe'
        CommandLine = 'pythonw.exe -m pytools.media_remote serve'
    },
    [pscustomobject]@{
        ProcessId = 4
        Name = 'unknown.exe'
        ExecutablePath = 'C:\\tools\\pytools\\Scripts\\unknown.exe'
        CommandLine = 'unknown.exe'
    }
"""
    mixed, mixed_output = run_reinstall_scenario("mixed-locking-processes", mixed_fixture)
    assert mixed == {
        "Locking": [2, 3, 4],
        "Restorable": [2, 3],
        "Unrestorable": [4],
        "Stopped": [],
        "MediaRemoteWasRunning": False,
        "ReinstallDeferred": True,
        "GetCimCalls": 1,
    }
    assert "  [pytools] 更新対象の使用中プロセス: 候補3件 再起動可2件 再起動不可1件" in mixed_output

    command_line_only_fixture = """    [pscustomobject]@{
        ProcessId = 5
        Name = 'pythonw.exe'
        ExecutablePath = $null
        CommandLine = 'pythonw.exe -m pytools.media_remote serve'
    }
"""
    command_line_only, command_line_only_output = run_reinstall_scenario(
        "command-line-only-media-remote", command_line_only_fixture
    )
    assert command_line_only == {
        "Locking": [5],
        "Restorable": [5],
        "Unrestorable": [],
        "Stopped": [5],
        "MediaRemoteWasRunning": True,
        "ReinstallDeferred": False,
        "GetCimCalls": 1,
    }
    assert "  [pytools] 更新対象の使用中プロセス: 候補1件 再起動可1件 再起動不可0件" in command_line_only_output

    empty, empty_output = run_reinstall_scenario("no-locking-processes", "")
    assert empty == {
        "Locking": [],
        "Restorable": [],
        "Unrestorable": [],
        "Stopped": [],
        "MediaRemoteWasRunning": False,
        "ReinstallDeferred": False,
        "GetCimCalls": 1,
    }
    assert empty_output == ["  [pytools] 更新対象の使用中プロセス: 候補0件 再起動可0件 再起動不可0件"]

    treatment_start = text.index("if ($env:COMPUTERNAME -eq 'stheno' -and -not $needsReinstall) {")
    treatment_end = text.index("\n\n# uv tool update-shell", treatment_start)
    treatment = text[treatment_start:treatment_end]

    def run_treatment_scenario(name: str, fixtures: str) -> tuple[dict[str, object], list[str]]:
        return run_scenario(
            f"{name}-treatment",
            fixtures,
            """$needsReinstall = $false
$env:COMPUTERNAME = 'stheno'
$env:LOCALAPPDATA = '/tmp'
$uvToolDir = 'C:\\tools\\pytools'
$mediaRemoteBin = 'C:\\Users\\test\\.local\\bin\\dotfiles-media-remote.exe'
function Test-Path { return $false }
""",
            "$allCandidates = @(Get-PytoolsEnvLockingProcess)\n" + treatment,
            """[pscustomobject]@{
    Candidates = @($allCandidates | ForEach-Object { $_.ProcessId })
    MediaRemote = @($mediaRemoteProcs | ForEach-Object { $_.ProcessId })
    Stopped = @($stopped)
    GetCimCalls = $getCimCalls
} | ConvertTo-Json -Compress
""",
        )

    distributed_treatment, distributed_treatment_output = run_treatment_scenario(
        "distributed-media-remote", distributed_fixture
    )
    mixed_treatment, mixed_treatment_output = run_treatment_scenario("mixed-locking-processes", mixed_fixture)
    command_line_treatment, command_line_treatment_output = run_treatment_scenario(
        "command-line-only-media-remote", command_line_only_fixture
    )
    empty_treatment, empty_treatment_output = run_treatment_scenario("no-locking-processes", "")

    assert distributed_treatment["Candidates"] == distributed_exe["Locking"]
    assert mixed_treatment["Candidates"] == mixed["Locking"]
    assert command_line_treatment["Candidates"] == command_line_only["Locking"]
    assert empty_treatment["Candidates"] == empty["Locking"]
    assert distributed_treatment == {"Candidates": [1], "MediaRemote": [1], "Stopped": [1], "GetCimCalls": 2}
    assert mixed_treatment == {"Candidates": [2, 3, 4], "MediaRemote": [2, 3], "Stopped": [2, 3], "GetCimCalls": 2}
    assert command_line_treatment == {"Candidates": [5], "MediaRemote": [5], "Stopped": [5], "GetCimCalls": 2}
    assert empty_treatment == {"Candidates": [], "MediaRemote": [], "Stopped": [], "GetCimCalls": 2}
    assert "  [pytools] 対症療法の停止対象プロセス: 停止対象1件" in distributed_treatment_output
    assert "  [pytools] 対症療法の停止対象プロセス: 停止対象2件" in mixed_treatment_output
    assert "  [pytools] 対症療法の停止対象プロセス: 停止対象1件" in command_line_treatment_output
    assert empty_treatment_output == ["  [pytools] 対症療法の停止対象プロセス: 停止対象0件"]


@pytest.mark.parametrize(
    ("template", "suffix", "pattern"),
    [
        (WINDOWS_TEMPLATE, ".exe", r"^    '([^']+)'$"),
        (LINUX_TEMPLATE, "", r'^    "([^"]+)"$'),
    ],
)
def test_expected_shims_cover_project_scripts(template: Path, suffix: str, pattern: str) -> None:
    """両テンプレートの期待シムをproject.scriptsの全キーから導出する。"""
    completed = subprocess.run(
        [
            "chezmoi",
            "--source",
            str(REPO_ROOT / ".chezmoi-source"),
            "--working-tree",
            str(REPO_ROOT),
            "execute-template",
            "--file",
            str(template),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    expected = {f"{name}{suffix}" for name in scripts}
    actual = set(re.findall(pattern, completed.stdout, flags=re.MULTILINE))
    assert actual == expected


def test_linux_install_failure_preserves_hash_and_continues() -> None:
    """Linux側は更新対象を保持するプロセスを停止せず、成功時だけハッシュを更新する。"""
    text = _read(LINUX_TEMPLATE)
    install = text.index(
        'if uv tool install --editable "{{ .chezmoi.workingTree }}" '
        '--with-editable "{{ joinPath .chezmoi.workingTree "agent-toolkit" }}" && test_expected_shims; then'
    )
    hash_write = text.index('printf \'%s\' "$current_hash" >"$hash_file"')
    failure = text.index("インストールに失敗しました", hash_write)

    assert "プロセスを停止せず" in text[:install]
    assert install < hash_write < failure
    assert 'DOTFILES_PYTOOLS_INSTALL_STATE="failed"' in text[hash_write:failure]
    assert "export DOTFILES_PYTOOLS_INSTALL_STATE DOTFILES_PYTOOLS_INSTALL_DETAIL" in text[failure:]
