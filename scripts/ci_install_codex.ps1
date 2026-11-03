Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$npmVersion = npm.cmd --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "npm: $npmVersion"
npm.cmd install --global --no-audit --no-fund @openai/codex@0.163.0
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$npmRoot = npm.cmd root --global
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# asyncioの子プロセス起動は、npmのcmd shimを介さず同じ配布物のnative executableを使う。
$binaries = @(Get-ChildItem -Path (Join-Path $npmRoot '@openai') -Filter codex.exe -File -Recurse)
if ($binaries.Count -ne 1) { throw "codex.exeを一意に解決できません: $($binaries.Count)件" }
$version = & $binaries[0].FullName --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output $version
if ($version -ne 'codex-cli 0.163.0') { throw "検証対象のCodex版が一致しません: $version" }
$binaries[0].DirectoryName | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8
