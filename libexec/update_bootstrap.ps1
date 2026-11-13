param([Parameter(Mandatory = $true)][string]$UvPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:bootstrapFailures = @()
. (Join-Path $PSScriptRoot 'locking_process.ps1')
function Invoke-UpdateStage {
    param([string]$Name, [string]$Classification, [int]$Seconds, [string]$Program, [string[]]$Arguments, [switch]$Probe)
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $Program
    $process.StartInfo.Arguments = ($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    try {
        [void]$process.Start()
        if (-not $process.WaitForExit($Seconds * 1000)) {
            & taskkill.exe /PID $process.Id /T /F | Out-Host
            if (-not $process.WaitForExit(10000)) { throw '時間切れの子孫を回収できませんでした。表示されたPIDを確認してください。' }
            $code = 124
        } else { $code = $process.ExitCode }
    } catch {
        [Console]::Error.WriteLine("${Name}: $_")
        $code = 1
    } finally { $process.Dispose() }
    if ($code -ne 0) {
        $script:bootstrapFailures += @{title=$Name; detail="exit=$code"}
        [Console]::Error.WriteLine("${Name}に失敗しました（exit=$code）。既存版で更新を続け、次回起動時に再試行します。")
    }
    if ($Classification -eq 'abort' -and $code -ne 0 -and -not $Probe) { throw "${Name}を完了できません。通信の問題を解消してupdate-dotfilesを再実行してください。" }
    return $code
}
try {
    if (-not (Test-Path -LiteralPath $UvPath)) {
        $env:UV_INSTALL_DIR = Split-Path -Parent $UvPath
        $env:UV_NO_MODIFY_PATH = '1'
        # 自己更新補助と同じ公式の導入経路を使う。
        $installer = 'Invoke-RestMethod -Uri https://astral.sh/uv/install.ps1 -TimeoutSec 120 | Invoke-Expression'
        try { [void](Invoke-UpdateStage 'uvの導入' abort 300 'powershell.exe' @('-NoProfile', '-Command', $installer)) }
        catch { [void](Invoke-UpdateStage 'uvの導入' abort 300 'powershell.exe' @('-NoProfile', '-Command', $installer)) }
    }
    try {
    if ($env:AGENT_TOOLKIT_PROCESS_LOOP_SESSION -ne '1') {
        [void](Invoke-UpdateStage 'uvの自己更新' continue 300 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'update_uv.ps1'), $UvPath))
        $excluded = @($PID)
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $ancestor = $PID
        while ($ancestor) {
            $parent = @($processes | Where-Object { $_.ProcessId -eq $ancestor })
            if (-not $parent.Count) { break }
            $ancestor = $parent[0].ParentProcessId
            $excluded += $ancestor
        }
        $pythonRoot = Join-Path $env:APPDATA 'uv\python'
        $locking = @(Get-PytoolsEnvLockingProcess -Directory $pythonRoot -ExcludeIds $excluded | Where-Object { $_.IsMediaRemote -or $_.ExecutablePath -match '\\cpython-3\.15[^\\]*\\' })
        $unrestorable = @($locking | Where-Object { -not $_.IsMediaRemote })
        $state = Join-Path $env:LOCALAPPDATA 'agent-toolkit\python-upgrade-deferred.txt'
        if ($unrestorable.Count) {
            $count = 0
            if (Test-Path $state) { [void][int]::TryParse((Get-Content $state -Raw), [ref]$count) }
            $count++
            [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $state))
            Set-Content -Path $state -Value $count -Encoding UTF8
            $detail = ($unrestorable | ForEach-Object { "$($_.Name) (PID=$($_.ProcessId))" }) -join ', '
            [Console]::Error.WriteLine("管理Pythonの更新を見送りました（連続${count}回）: $detail。既存版で続行し次回再試行します。")
        } else {
            try {
                foreach ($item in $locking) { Stop-Process -Id $item.ProcessId -Force -ErrorAction Stop }
                $code = Invoke-UpdateStage '管理Pythonの更新' continue 900 $UvPath @('python', 'install', '--upgrade', '3.15')
                if ($code -eq 0) { Remove-Item -LiteralPath $state -ErrorAction SilentlyContinue }
            } finally {
                if ($locking.Count) {
                    $env:DOTFILES_MEDIA_REMOTE_STOPPED = '1'
                    # 起動処理はpost-applyと同じPython実装へ委ねる。
                    $toolDir = & $UvPath tool dir
                    if ($LASTEXITCODE -ne 0) { throw 'pytoolsの導入先を取得できません。update-dotfilesを再実行してください。' }
                    & (Join-Path $toolDir 'pytools\Scripts\python.exe') -c 'from pytools._internal.setup_media_remote import run; run()'
                    if ($LASTEXITCODE -ne 0) { throw 'メディアリモコンを再起動できません。dotfiles-post-applyを実行してください。' }
                }
            }
        }
    }
    } catch {
        [Console]::Error.WriteLine("管理Pythonの更新を完了できませんでした: $_。既存版で続行し、次回起動時に再試行します。")
    }
    $findCode = Invoke-UpdateStage '管理Pythonの導入' abort 60 $UvPath @('python', 'find', '3.15') -Probe
    if ($findCode -ne 0) {
        try { [void](Invoke-UpdateStage '管理Pythonの導入' abort 900 $UvPath @('python', 'install', '3.15')) }
        catch { [void](Invoke-UpdateStage '管理Pythonの導入' abort 900 $UvPath @('python', 'install', '3.15')) }
        $findCode = Invoke-UpdateStage '管理Pythonの導入' abort 60 $UvPath @('python', 'find', '3.15') -Probe
        if ($findCode -ne 0) { throw '使える3.15がありません。uv python install 3.15を実行してください。' }
    }
} catch {
    [Console]::Error.WriteLine("$_")
    exit 1
}
if ($env:UPDATE_DOTFILES_BOOTSTRAP_REPORT -and $script:bootstrapFailures.Count) {
    try {
        [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $env:UPDATE_DOTFILES_BOOTSTRAP_REPORT))
        ConvertTo-Json -InputObject @($script:bootstrapFailures) -Compress |
            Set-Content -LiteralPath $env:UPDATE_DOTFILES_BOOTSTRAP_REPORT -Encoding UTF8
    } catch { [Console]::Error.WriteLine("更新失敗の記録を書けませんでした: $_") }
}
exit 0
