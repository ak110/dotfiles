param([Parameter(Mandatory = $true)][string]$UvPath, [switch]$PrepareOnly, [switch]$PrintPython)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:bootstrapFailures = @()
$script:lastStageOutput = ''
. (Join-Path $PSScriptRoot 'locking_process.ps1')
function Write-UpdateError {
    param([string]$Message)
    if ([Console]::IsErrorRedirected) {
        $writer = [System.IO.StreamWriter]::new([Console]::OpenStandardError(), [System.Text.UTF8Encoding]::new($false))
        try { $writer.WriteLine($Message) } finally { $writer.Dispose() }
    } else { [Console]::Error.WriteLine($Message) }
}
function Invoke-UpdateStage {
    param([string]$Name, [string]$Classification, [int]$Seconds, [string]$Program, [string[]]$Arguments, [switch]$Probe)
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $Program
    $process.StartInfo.Arguments = ($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    if ($Probe) {
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    }
    try {
        [void]$process.Start()
        # 終了と列挙が競合しても、回収完了まで起点PIDを別世代へ再利用させない。
        [void]$process.Handle
        if ($Probe) { $outputTask = $process.StandardOutput.ReadToEndAsync() }
        if (-not $process.WaitForExit($Seconds * 1000)) {
            try {
                $owned = @{}
                $owned[$process.Id] = $process
                try {
                    # Windowsは親の終了後もPPIDを保持する。世代を保持した全起点から毎回辿り直す。
                    for ($round = 0; $round -lt 10; $round++) {
                        $entries = @(CimCmdlets\Get-CimInstance Win32_Process -OperationTimeoutSec 10 -ErrorAction Stop)
                        do {
                            $added = $false
                            foreach ($entry in $entries) {
                                if ($owned.ContainsKey([int]$entry.ProcessId) -or -not $owned.ContainsKey([int]$entry.ParentProcessId)) { continue }
                                $parent = $owned[[int]$entry.ParentProcessId]
                                if ($entry.CreationDate.ToUniversalTime() -lt $parent.StartTime.ToUniversalTime()) { continue }
                                $child = $null
                                $retained = $false
                                try {
                                    $child = [System.Diagnostics.Process]::GetProcessById([int]$entry.ProcessId)
                                    [void]$child.Handle
                                    # CIMの時刻はマイクロ秒までなので、100ns単位のStartTimeを同じ精度へそろえる。
                                    $createdTicks = $child.StartTime.ToUniversalTime().Ticks
                                    if (($createdTicks - ($createdTicks % 10)) -ne $entry.CreationDate.ToUniversalTime().Ticks) { continue }
                                    $owned[$child.Id] = $child
                                    $retained = $true
                                    $added = $true
                                } catch [System.ArgumentException] {
                                    # snapshot取得後に消滅した候補への停止は不要。再利用先は次の一覧で所属を判定する。
                                    continue
                                } catch {
                                    if ($null -ne $child -and $child.HasExited) { continue }
                                    throw
                                } finally {
                                    if ($null -ne $child -and -not $retained) { $child.Dispose() }
                                }
                            }
                        } while ($added)
                        $alive = @($owned.Values | Where-Object { -not $_.HasExited })
                        if (-not $alive.Count) { break }
                        foreach ($target in $alive) {
                            try { $target.Kill() }
                            catch { if (-not $target.HasExited) { throw } }
                        }
                        foreach ($target in $alive) {
                            if (-not $target.WaitForExit(1000)) { throw "PID=$($target.Id)が残っています" }
                        }
                    }
                    if ($round -eq 10) { throw '子孫の生成が止まりませんでした' }
                } finally {
                    foreach ($target in $owned.Values) { if ($target -ne $process) { $target.Dispose() } }
                }
            } catch {
                throw [System.TimeoutException]::new("PID=$($process.Id)の子孫を回収できません。表示されたPIDを確認して終了させてください: $_")
            }
            $code = 124
        } else { $code = $process.ExitCode }
        if ($Probe) {
            if ($outputTask.Wait(1000)) { $script:lastStageOutput = $outputTask.Result.Trim() }
            else { $code = 1 }
        }
    } catch [System.TimeoutException] { throw }
    catch {
        Write-UpdateError ("${Name}: $_")
        $code = 1
    } finally { $process.Dispose() }
    if ($code -ne 0) {
        $script:bootstrapFailures += @{title=$Name; detail="exit=$code"}
        Write-UpdateError ("${Name}に失敗しました（exit=$code）。既存版で更新を続け、次回起動時に再試行します。")
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
        catch [System.TimeoutException] { throw }
        catch { [void](Invoke-UpdateStage 'uvの導入' abort 300 'powershell.exe' @('-NoProfile', '-Command', $installer)) }
    }
    try {
    if (-not $PrepareOnly -and $env:AGENT_TOOLKIT_PROCESS_LOOP_SESSION -ne '1') {
        [void](Invoke-UpdateStage 'uvの自己更新' continue 300 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'update_uv.ps1'), $UvPath))
        $excluded = @($PID)
        $processes = @(Get-CimInstance Win32_Process -OperationTimeoutSec 30 -ErrorAction Stop)
        $ancestor = $PID
        while ($ancestor) {
            $parent = @($processes | Where-Object { $_.ProcessId -eq $ancestor })
            if (-not $parent.Count) { break }
            $ancestor = $parent[0].ParentProcessId
            $excluded += $ancestor
        }
        $directoryCode = Invoke-UpdateStage '管理Pythonの更新' continue 60 $UvPath @('python', 'dir') -Probe
        if ($directoryCode -ne 0 -or -not $script:lastStageOutput) { throw '管理Pythonの導入先を取得できないため更新を見送ります。' }
        $pythonRoot = $script:lastStageOutput
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
            $script:bootstrapFailures += @{title='管理Pythonの更新'; detail="使用中のため見送り: $detail"}
            Write-UpdateError ("管理Pythonの更新を見送りました（連続${count}回）: $detail。既存版で続行し次回再試行します。")
        } else {
            $stopped = @()
            try {
                $stopped = @($locking)
                Invoke-PytoolsMediaRemoteStop -Processes $locking
                $code = Invoke-UpdateStage '管理Pythonの更新' continue 900 $UvPath @('python', 'install', '--upgrade', '3.15')
                if ($code -eq 0) { Remove-Item -LiteralPath $state -ErrorAction SilentlyContinue }
            } finally {
                if ($stopped.Count) {
                    $env:DOTFILES_MEDIA_REMOTE_STOPPED = '1'
                    # 起動処理はpost-applyと同じPython実装へ委ねる。
                    $toolCode = Invoke-UpdateStage '管理Pythonの更新' continue 60 $UvPath @('tool', 'dir') -Probe
                    if ($toolCode -ne 0) { throw 'pytoolsの導入先を取得できません。update-dotfilesを再実行してください。' }
                    $toolDir = $script:lastStageOutput
                    $restartCode = Invoke-UpdateStage '管理Pythonの更新' continue 60 (Join-Path $toolDir 'pytools\Scripts\python.exe') @(
                        '-c', 'from pytools._internal.setup_media_remote import run; outcome=run(); print(outcome.failure or str()); raise SystemExit(bool(outcome.failure))'
                    )
                    if ($restartCode -ne 0) { throw 'メディアリモコンを再起動できません。dotfiles-post-applyを実行してください。' }
                }
            }
        }
    }
    } catch [System.TimeoutException] { throw }
    catch {
        $script:bootstrapFailures += @{title='管理Pythonの更新'; detail="$_"}
        Write-UpdateError ("管理Pythonの更新を完了できませんでした: $_。既存版で続行し、次回起動時に再試行します。")
    }
    $findCode = Invoke-UpdateStage '管理Pythonの導入' abort 60 $UvPath @('python', 'find', '3.15') -Probe
    if ($findCode -ne 0) {
        try { [void](Invoke-UpdateStage '管理Pythonの導入' abort 900 $UvPath @('python', 'install', '3.15')) }
        catch [System.TimeoutException] { throw }
        catch { [void](Invoke-UpdateStage '管理Pythonの導入' abort 900 $UvPath @('python', 'install', '3.15')) }
        $findCode = Invoke-UpdateStage '管理Pythonの導入' abort 60 $UvPath @('python', 'find', '3.15') -Probe
        if ($findCode -ne 0) { throw '使える3.15がありません。uv python install 3.15を実行してください。' }
    }
    if (@($script:bootstrapFailures | Where-Object { $_.title -eq '管理Pythonの更新' }).Count -and
        $script:lastStageOutput -match '3\.15\.\d+(a|b|rc)\d+') {
        Write-UpdateError ("残った管理Pythonはpre-releaseです: $($script:lastStageOutput)。次回起動時に更新を再試行します。")
    }
} catch {
    Write-UpdateError ("$_")
    exit 1
}
if ($env:UPDATE_DOTFILES_BOOTSTRAP_REPORT -and $script:bootstrapFailures.Count) {
    try {
        [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $env:UPDATE_DOTFILES_BOOTSTRAP_REPORT))
        ConvertTo-Json -InputObject @($script:bootstrapFailures) -Compress |
            Set-Content -LiteralPath $env:UPDATE_DOTFILES_BOOTSTRAP_REPORT -Encoding UTF8
    } catch { Write-UpdateError ("更新失敗の記録を書けませんでした（保存先: $env:UPDATE_DOTFILES_BOOTSTRAP_REPORT）: $_。保存先の親ディレクトリの書込権限を修復してupdate-dotfilesを再実行してください。") }
}
if ($PrintPython) { [Console]::Out.WriteLine($script:lastStageOutput) }
exit 0
