Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# uv's exit status (including 75) is part of the launcher protocol.
$PSNativeCommandUseErrorActionPreference = $false

$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'agent_toolkit/atk.py'
$arguments = @($args)
$supervised = $arguments.Count -ge 2 -and $arguments[0] -ceq 'wi' -and $arguments[1] -ceq 'process-loop'
$previousSpec = $env:AGENT_TOOLKIT_RESTART_SPEC
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
        uv.exe run --no-cache --no-config --no-project --python 3.15 python $launch --prepare-if-missing $scriptPath @arguments
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
    if ($null -ne $spec) {
        $env:AGENT_TOOLKIT_RESTART_SPEC = $previousSpec
        Remove-Item -LiteralPath $spec -Force
    }
}
