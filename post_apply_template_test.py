"""post-applyテンプレートの最終実行順序を検証する。"""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from pytools._internal import setup_media_remote

pytestmark = pytest.mark.windows

REPO_ROOT = Path(__file__).resolve().parent
LINUX_TEMPLATE = REPO_ROOT / ".chezmoi-source/run_after_post-apply.sh.tmpl"
WINDOWS_TEMPLATE = REPO_ROOT / ".chezmoi-source/run_after_post-apply-windows.ps1.tmpl"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


def _read(path: Path) -> str:
    """BOMの有無を許容してテンプレートを読む。"""
    return path.read_text(encoding="utf-8-sig")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell実行体が見つからない")
def test_windows_post_apply_failure_exit_propagates(tmp_path: Path) -> None:
    """post-applyが失敗したとき、Windowsテンプレートの最終分岐も非0で終了する。"""
    powershell = POWERSHELL
    assert powershell is not None
    post_apply = tmp_path / "failing-post-apply.ps1"
    post_apply.write_text("exit 23\n", encoding="utf-8-sig")
    text = _read(WINDOWS_TEMPLATE)
    final_start = text.rindex("$postApplyBin = Join-Path $env:USERPROFILE '.local\\bin\\dotfiles-post-apply.exe'")
    final_block = text[final_start:]
    assignment = final_block.splitlines()[0]
    escaped_path = str(post_apply).replace("'", "''")
    final_block = final_block.replace(assignment, f"$postApplyBin = '{escaped_path}'", 1)
    # テンプレートは本体をtryの内側へ置き、finallyで出力の文字コードを戻す。
    # exitがfinallyを経ても終了コードを保つことを確かめる。
    runner = tmp_path / "run-post-apply.ps1"
    runner.write_text("$originalOutputEncoding = [Console]::OutputEncoding\ntry {\n" + final_block, encoding="utf-8-sig")

    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 23, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1の出力の文字コードはWindowsでだけ観測できる")
def test_windows_template_writes_utf8_when_redirected(tmp_path: Path) -> None:
    """テンプレートが設定する文字コードの下で、リダイレクトしたWrite-Hostの日本語をUTF-8として読める。"""
    text = _read(WINDOWS_TEMPLATE)
    prologue = text[text.index("$originalOutputEncoding = [Console]::OutputEncoding") : text.index("try {")]
    message = "[pytools] 日本語の案内"
    script = tmp_path / "encoding.ps1"
    script.write_text(
        prologue + f"try {{\nWrite-Host '{message}'\n}} finally {{\n[Console]::OutputEncoding = $originalOutputEncoding\n}}\n",
        encoding="utf-8-sig",
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8").strip() == message


def test_windows_template_limits_to_reinstall() -> None:
    """テンプレートはpytoolsの再導入とその前の停止だけを持ち、user.envの反映とmedia-remoteの起動を持たない。

    再起動の要否はpost-applyの`setup_media_remote`工程が環境変数から読むため、変数名をその定義と比べる。
    """
    text = _read(WINDOWS_TEMPLATE)

    assert "user.env" not in text
    assert "wscript" not in text
    assert "'serve'" not in text
    assert "launch.vbs" not in text
    assert "Stop-Process -Id $p.ProcessId" in text
    assert f"$env:{setup_media_remote.STOPPED_ENV} = " in text
    assert f"$env:{setup_media_remote.REINSTALL_ENV} = " in text
    # 渡した後にpost-applyを起動する。
    assert text.index(f"$env:{setup_media_remote.STOPPED_ENV} = ") < text.index("& $postApplyBin")


def test_windows_reinstall_defers_without_stopping_unrestorable_processes() -> None:
    """復元不能なlocking processがある場合は停止と再導入を行わず、後続処理を継続する。"""
    text = _read(WINDOWS_TEMPLATE)

    classification = text.index("$unrestorableProcs += $p")
    guard = text.index("if ($unrestorableProcs.Count -gt 0)")
    stop_loop = text.index("foreach ($p in $restorableProcs)")
    stop_process = text.index("Stop-Process -Id $p.ProcessId", stop_loop)
    install_guard = text.index("if ($needsReinstall -and -not $reinstallDeferred)")
    install = text.index('& uv tool install --exclude-newer-package pyfltr=false --editable "{{ .chezmoi.workingTree }}"')
    deferred = text.index("} elseif ($reinstallDeferred) {")

    assert classification < guard < stop_loop < stop_process < install_guard < install < deferred
    assert "$reinstallDeferred = $true" in text[guard:stop_loop]
    assert "$env:DOTFILES_PYTOOLS_INSTALL_STATE = 'deferred'" in text[guard:stop_loop]
    assert "$env:DOTFILES_PYTOOLS_INSTALL_DETAIL" in text[guard:stop_loop]
    assert "Stop-Process" not in text[guard:stop_loop]
    assert "再インストールを次回へ延期" in text[guard:stop_loop]
    assert "既存版で後続処理を継続" in text[deferred:]
    assert "locking_process.ps1" in text
    assert text.count("Get-PytoolsEnvLockingProcess") == 1


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell実行体が見つからない")
def test_windows_locking_processes_classify_and_record_restart_state(
    tmp_path: Path,
) -> None:
    """配布exeとmoduleを分類し、停止前の再起動対象を正しく記録する。"""
    powershell = POWERSHELL
    assert powershell is not None
    text = _read(WINDOWS_TEMPLATE)
    prologue = text[text.index("$originalOutputEncoding = [Console]::OutputEncoding") : text.index("try {")]
    classifier = (WINDOWS_TEMPLATE.parent.parent / "libexec/locking_process.ps1").read_text(encoding="utf-8-sig")
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
            + prologue
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
            encoding="utf-8",
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
        encoding="utf-8",
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
        "if uv tool install --exclude-newer-package pyfltr=false "
        '--editable "{{ .chezmoi.workingTree }}" '
        '--with-editable "{{ joinPath .chezmoi.workingTree "agent-toolkit" }}" && test_expected_shims; then'
    )
    hash_write = text.index('printf \'%s\' "$current_hash" >"$hash_file"')
    failure = text.index("インストールに失敗しました", hash_write)

    assert "プロセスを停止せず" in text[:install]
    assert install < hash_write < failure
    assert 'DOTFILES_PYTOOLS_INSTALL_STATE="failed"' in text[hash_write:failure]
    assert "export DOTFILES_PYTOOLS_INSTALL_STATE DOTFILES_PYTOOLS_INSTALL_DETAIL" in text[failure:]


@pytest.mark.skipif(POWERSHELL is None or os.name == "nt", reason="LinuxのpwshでWindowsプロセスを模擬する")
@pytest.mark.parametrize("blocked,upgrade_exit", [(False, 0), (False, 17), (True, 0)])
def test_windows_python_bootstrap_process_ownership(tmp_path: Path, blocked: bool, upgrade_exit: int) -> None:
    """使用中の分類から停止・更新・復帰、または見送りまでを実行する。"""
    log = tmp_path / "calls"
    fake_uv = tmp_path / "uv"
    tool_python = tmp_path / "tools/pytools/Scripts/python.exe"
    tool_python.parent.mkdir(parents=True)
    tool_python.write_text('#!/usr/bin/env bash\nprintf "restart\\n" >> "$BOOTSTRAP_LOG"\n', encoding="utf-8")
    tool_python.chmod(0o755)
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1 $2" == "python install" ]]; then printf "upgrade\\n" >> "$BOOTSTRAP_LOG"; exit "$UPGRADE_EXIT"; fi\n'
        'if [[ "$1 $2" == "tool dir" ]]; then printf "%s\\n" "$TOOL_BASE"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    powershell_stub = tmp_path / "powershell.exe"
    powershell_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    powershell_stub.chmod(0o755)
    driver = tmp_path / "driver.ps1"
    command = "arbitrary-python" if blocked else "pytools.media_remote"
    driver.write_text(
        "New-PSDrive -Name C -PSProvider FileSystem -Root $env:BOOTSTRAP_ROOT | Out-Null\n"
        "$env:APPDATA = 'C:\\appdata'\n"
        "function global:Join-Path { param([string]$Path,[string]$ChildPath)\n"
        "if ($Path -like 'C:*') { return $Path.TrimEnd('\\') + '\\' + $ChildPath }; "
        "Microsoft.PowerShell.Management\\Join-Path $Path $ChildPath }\n"
        "function global:Get-CimInstance { param([string]$ClassName)\n"
        "[pscustomobject]@{ ProcessId=101; ParentProcessId=0; Name='python.exe'; "
        f"ExecutablePath='C:\\appdata\\uv\\python\\cpython-3.15.2\\python.exe'; CommandLine='{command}' }}\n"
        "}\n"
        'function global:Stop-Process { param([int]$Id,[switch]$Force) Add-Content $env:BOOTSTRAP_LOG "stop:$Id" }\n'
        f"& '{REPO_ROOT / 'libexec/update_bootstrap.ps1'}' '{fake_uv}'\n",
        encoding="utf-8-sig",
    )
    environment = os.environ.copy()
    environment.update(
        PATH=str(tmp_path) + os.pathsep + environment["PATH"],
        APPDATA=str(tmp_path / "appdata"),
        LOCALAPPDATA=str(tmp_path / "state"),
        USERPROFILE=str(tmp_path / "home"),
        BOOTSTRAP_ROOT=str(tmp_path),
        BOOTSTRAP_LOG=str(log),
        TOOL_BASE=str(tmp_path / "tools"),
        UPGRADE_EXIT=str(upgrade_exit),
        AGENT_TOOLKIT_PROCESS_LOOP_SESSION="0",
    )
    result = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-File", str(driver)],
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    if blocked:
        assert not log.exists()
        assert "連続1回" in result.stderr
        assert "PID=101" in result.stderr
    else:
        assert log.read_text(encoding="utf-8").splitlines() == ["stop:101", "upgrade", "restart"]
