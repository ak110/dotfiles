# 公式installerでuvの導入先の記録を現在の実体に合わせる。
[System.Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingInvokeExpression', '', Justification = 'uv公式installerが提供するPowerShell本文を実行する専用経路である。')]
param([Parameter(Mandatory = $true)][string]$UvPath)
# 親の段がUTF-8のパイプでstdout/stderrを回収する。
$originalEncoding = [Console]::OutputEncoding
try {
    if ([Console]::IsOutputRedirected) { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) }
    $ErrorActionPreference = 'Stop'
    # 環境変数はこの子プロセスだけで使い、ユーザーのshell設定は変更しない。
    Write-Host 'uvの導入先の記録を現在の実体へそろえるため、公式installerで再導入する。'
    Remove-Item Env:UV_UNMANAGED_INSTALL -ErrorAction SilentlyContinue
    $env:UV_INSTALL_DIR = Split-Path -Parent $UvPath
    $env:UV_NO_MODIFY_PATH = '1'
    try {
        Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' -TimeoutSec 120 | Invoke-Expression
    } catch {
        Write-Error "uvの再導入に失敗した: $_"
        exit 1
    }
    exit 0
} finally {
    [Console]::OutputEncoding = $originalEncoding
}
