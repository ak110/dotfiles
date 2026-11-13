"""PS側の停止が、消滅とPID再利用を区別して更新対象だけを扱うことを検証する。"""

import json
import pathlib
import shutil
import subprocess
from collections.abc import Callable

import pytest

_POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


@pytest.mark.parametrize("mode", ["gone", "current", "reused"])
@pytest.mark.skipif(_POWERSHELL is None, reason="PowerShell実行体が必要")
def test_media_stop_uses_current_generation_and_accepts_disappearance(
    tmp_path: pathlib.Path, mode: str, host_environ: Callable[[], dict[str, str]]
) -> None:
    """同じ個体だけを停止し、一覧から消えた対象は成功、別世代は更新見送りにする。"""
    script = tmp_path / "stop.ps1"
    helper = pathlib.Path(__file__).with_name("locking_process.ps1")
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + f". '{str(helper).replace(chr(39), chr(39) * 2)}'\n"
        + f"$mode = '{mode}'\n"
        + """
$script:killed = @()
$date = [DateTime]'2026-11-14T01:02:03.123456Z'
function Get-Process {
    [CmdletBinding()]
    param([int]$Id)
    if ($mode -eq 'gone') { return $null }
    $start = if ($mode -eq 'reused') { $date.AddSeconds(1) } else { $date.AddTicks(9) }
    $process = [pscustomobject]@{ Id=$Id; Handle=1; HasExited=$false; StartTime=$start }
    $process | Add-Member ScriptMethod Kill { $script:killed += $this.Id; $this.HasExited=$true }
    $process | Add-Member ScriptMethod WaitForExit { param($Timeout) return $this.HasExited }
    $process | Add-Member ScriptMethod Dispose { }
    return $process
}
$target = [pscustomobject]@{ProcessId=101; IsMediaRemote=$true; CreationDate=$date}
$failed = $false
try { Invoke-PytoolsMediaRemoteStop -Processes @($target) } catch { $failed = $true }
[pscustomobject]@{ Killed=@($script:killed); Failed=$failed } | ConvertTo-Json -Compress
""",
        encoding="utf-8-sig",
    )
    environment = host_environ()
    environment["USERPROFILE"] = str(tmp_path)
    result = subprocess.run(
        [str(_POWERSHELL), "-NoProfile", "-File", str(script)],
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual == {"Killed": [101] if mode == "current" else [], "Failed": mode == "reused"}
