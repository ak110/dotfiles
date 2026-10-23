# uvの自己更新を行い、導入先の記録が異なる場合は公式installerで現行の導入先へ戻す。
[System.Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingInvokeExpression', '', Justification = 'uv公式installerが提供するPowerShell本文を実行する専用経路である。')]
param([Parameter(Mandatory = $true)][string]$UvPath)
$ErrorActionPreference = 'Continue'
$updateOutput = & $UvPath self update 2>&1
$updateStatus = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($updateStatus -eq 0) {
    $updateOutput | ForEach-Object { Write-Host "$_" }
    exit 0
}
if (($updateOutput -join "`n") -notlike '*but the standalone installer was used to install uv to*') {
    $updateOutput | ForEach-Object { [Console]::Error.WriteLine("$_") }
    exit 1
}
# 環境変数はこの子プロセスだけで使い、ユーザーのshell設定は変更しない。
Write-Host 'uvの導入先の記録を現在の実体へそろえるため、公式installerで再導入する。'
Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
$env:UV_INSTALL_DIR = Split-Path -Parent $UvPath
$env:UV_NO_MODIFY_PATH = '1'
try {
    Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -TimeoutSec 120 | Invoke-Expression
} catch {
    Write-Error "uvの再導入に失敗した: $_ / 元の自己更新結果: $($updateOutput -join "`n")"
    exit 1
}
exit 0
