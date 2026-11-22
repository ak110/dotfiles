Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root 'agent_toolkit/atk.py'
$launch = Join-Path $root 'scripts/launch.py'
$arguments = @($args)
# PowerShell treats a native argument equal to --% as syntax, even when splatted.
# Pass the argument file to the native binder.
$bootstrap = @'
import json, runpy, sys
from pathlib import Path
launch, target, arguments = sys.argv[1:]
sys.path.insert(0, str(Path(launch).parent))
sys.argv = [launch, '--prepare-if-missing', target, *json.loads(Path(arguments).read_text(encoding='utf-8'))]
runpy.run_path(launch, run_name='__main__')
'@
$argumentsFile = [IO.Path]::GetTempFileName()
try {
    [IO.File]::WriteAllText($argumentsFile, (ConvertTo-Json -Compress -InputObject ([string[]]$arguments)))
    uv.exe run --no-cache --no-config --no-project --python 3.15 python -P -c $bootstrap $launch $scriptPath $argumentsFile
    exit $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $argumentsFile -Force
}
