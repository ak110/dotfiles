# ssh-unelevatedの予定タスクが、ログオン中のユーザーの非昇格のtokenで実行する。
# 同じディレクトリのscript.ps1を実行し、出力をoutput.txtへ、終了コードをexit.txtへ残す。
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# 外部コマンドとの入出力をUTF-8にする（既定のコードページのままでは日本語が崩れる）。
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$scriptPath = Join-Path $PSScriptRoot 'script.ps1'
$output = Join-Path $PSScriptRoot 'output.txt'
try {
    # スクリプトはPowerShellの既定（strict modeなし、エラーで止めない）で動かす。
    # スクリプトファイルとして呼ぶと、exitはスクリプトだけを終えて$LASTEXITCODEへ値を残す。
    & {
        Set-StrictMode -Off
        $ErrorActionPreference = 'Continue'
        & $scriptPath
    } *>&1 | Out-File -LiteralPath $output -Encoding utf8 -Width 4096
    $code = if (Test-Path variable:global:LASTEXITCODE) { $global:LASTEXITCODE } else { 0 }
} catch {
    $_ | Out-File -LiteralPath $output -Encoding utf8 -Width 4096 -Append
    $code = 1
}
# 待っている側が書きかけの値を読まないよう、書き終えてから名前を付ける。
$exitFile = Join-Path $PSScriptRoot 'exit.txt'
Set-Content -LiteralPath "$exitFile.tmp" -Value $code -Encoding ascii
Move-Item -LiteralPath "$exitFile.tmp" -Destination $exitFile
