Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# uv's exit status (including 75) is part of the launcher protocol.
$PSNativeCommandUseErrorActionPreference = $false

$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'agent_toolkit/atk.py'
$arguments = @($args)
$supervised = $arguments.Count -ge 2 -and $arguments[0] -ceq 'wi' -and $arguments[1] -ceq 'process-loop'
$previousSpec = $env:AGENT_TOOLKIT_RESTART_SPEC
# PowerShell treats a native argument equal to --% as syntax, even when splatted.
# Pass paths, not user arguments, to the native binder. Keep launch.py's argv
# contract unchanged, including when a restart selects a different release.
$bootstrap = @'
import json, runpy, sys
from pathlib import Path
launch, target, arguments = sys.argv[1:]
sys.path.insert(0, str(Path(launch).parent))
sys.argv = [launch, '--prepare-if-missing', target, *json.loads(Path(arguments).read_text(encoding='utf-8'))]
runpy.run_path(launch, run_name='__main__')
'@
$argumentsFile = [IO.Path]::GetTempFileName()
$spec = $null
try {
    if ($supervised) {
        $spec = [IO.Path]::GetTempFileName()
        $env:AGENT_TOOLKIT_RESTART_SPEC = $spec
    }
    while ($true) {
        if ($supervised) {
            [IO.File]::WriteAllText($spec, '')
        }
        $root = Split-Path -Parent (Split-Path -Parent $scriptPath)
        $launch = Join-Path $root 'scripts/launch.py'
        [IO.File]::WriteAllText($argumentsFile, (ConvertTo-Json -Compress -InputObject ([string[]]$arguments)))
        uv.exe run --no-cache --no-config --no-project --python 3.15 python -P -c $bootstrap $launch $scriptPath $argumentsFile
        $status = $LASTEXITCODE
        if (-not $supervised -or $status -ne 75) {
            exit $status
        }
        $next = [IO.File]::ReadAllLines($spec)
        if ($next.Count -eq 0 -or [string]::IsNullOrEmpty($next[0])) {
            exit $status
        }
        $scriptPath = $next[0]
        $arguments = @($next | Select-Object -Skip 1)
    }
} finally {
    try {
        if ($null -ne $spec) {
            $env:AGENT_TOOLKIT_RESTART_SPEC = $previousSpec
            Remove-Item -LiteralPath $spec -Force
        }
    } finally {
        Remove-Item -LiteralPath $argumentsFile -Force
    }
}
