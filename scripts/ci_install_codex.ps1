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
if ($binaries.Count -ne 1) { throw "codex.exeを一意に解決できません: $($binaries.Count)件。上記npm導入ログと${npmRoot}配下の@openaiパッケージを確認し、CLIの配置を修正後にRe-run failed jobsで再実行してください。" }
$version = & $binaries[0].FullName --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output $version
if ($version -ne 'codex-cli 0.163.0') { throw "検証対象のCodex版が一致しません: $version。解決した実行ファイル$($binaries[0].FullName)と上記npm導入ログを確認し、固定版0.163.0の導入を修正後にRe-run failed jobsで再実行してください。" }
$binaries[0].DirectoryName | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8
