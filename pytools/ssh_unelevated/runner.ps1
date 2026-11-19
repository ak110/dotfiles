# ssh-unelevatedの予定タスクが、ログオン中のユーザーの非昇格のtokenで実行する。
# 同じディレクトリのscript.ps1を実行し、出力をoutput.txtへ、終了コードをexit.txtへ残す。
param(
    # 予定タスクから起動されたことを示す。タスクのジョブの外で自身を起動し直し、その終了を待つ。
    [switch] $LeaveTaskJob
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$output = Join-Path $PSScriptRoot 'output.txt'
if ($LeaveTaskJob) {
    # タスクスケジューラーは、ユーザーのセッションで動く予定タスクのプロセスを共通の1つのジョブへ入れる。
    # そのジョブは子の切り離し（CREATE_BREAKAWAY_FROM_JOB）を許さないため、切り離して常駐させるプロセスの起動
    # （Codexのapp-server daemonなど）がアクセス拒否で失敗する。WMIで切り離しを指定して起動したプロセスは
    # どのジョブにも属さず、ユーザーが端末から実行した場合と同じくジョブに縛られずにスクリプトを実行できる（2026年11月19日、sthenoで観測）。
    try {
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ CreateFlags = [uint32] 0x01000000 }
        $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = "conhost.exe --headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`""
            CurrentDirectory = [Environment]::CurrentDirectory
            ProcessStartupInformation = $startup
        }
        if ($created.ReturnValue -ne 0) {
            throw "Win32_Process.Createが$($created.ReturnValue)を返した"
        }
    } catch {
        "ssh-unelevated: 予定タスクのジョブの外でスクリプトを起動できなかった: $_" | Out-File -LiteralPath $output -Encoding utf8 -Width 4096
        exit 1
    }
    # 既に終わっていれば待つものは無い。
    Wait-Process -Id $created.ProcessId -ErrorAction SilentlyContinue
    exit 0
}
# 外部コマンドとの入出力をUTF-8にする（既定のコードページのままでは日本語が崩れる）。
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$scriptPath = Join-Path $PSScriptRoot 'script.ps1'
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
