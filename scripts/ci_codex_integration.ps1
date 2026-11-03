Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
Write-Output "Codex境界pytest開始: at=$([DateTime]::UtcNow.ToString('o')) launcher_pid=$PID image=$env:ImageOS image_version=$env:ImageVersion"
codex --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --frozen --python 3.15 pytest -v -rA -p no:cacheprovider `
  agent-toolkit/agent_toolkit/_agents_server/codex_sandbox_integration_test.py::test_explore_start_reaches_normal_commands `
  agent-toolkit/agent_toolkit/_testing/resource_diagnostics_test.py
$pytestExitCode = $LASTEXITCODE
Write-Output "Codex境界pytest終了: at=$([DateTime]::UtcNow.ToString('o')) launcher_pid=$PID pytest_exit_code=$pytestExitCode"
exit $pytestExitCode
