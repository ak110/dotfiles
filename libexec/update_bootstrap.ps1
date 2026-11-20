param([Parameter(Mandatory = $true)][string]$UvPath, [switch]$PrepareOnly, [switch]$PrintPython)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:bootstrapFailures = @()
$script:lastStageOutput = ''
. (Join-Path $PSScriptRoot 'locking_process.ps1')
. (Join-Path $PSScriptRoot 'update_stage.ps1')
function Test-PythonPatchPending {
    # keyはビルド日付を含まず、同じpatch版の新しいビルドを検出できない。更新できない回の見送りの判定にだけ使う。
    $listed = @{}
    foreach ($source in '--only-downloads', '--only-installed') {
        $code = Invoke-UpdateStage '管理Pythonの更新' continue 60 $UvPath @('python', 'list', 'cpython-3.15', $source, '--managed-python', '--output-format', 'json') -Probe
        if ($code -ne 0) { return $true }
        $listed[$source] = @($script:lastStageOutput | ConvertFrom-Json | ForEach-Object { $_ } | ForEach-Object { $_.key })
    }
    return @($listed['--only-downloads'] | Where-Object { $_ -notin $listed['--only-installed'] }).Count -gt 0
}

try {
    if (-not (Test-Path -LiteralPath $UvPath)) {
        Write-UpdateError 'uvの導入: Ctrl+Cで中断できます。'
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
        Write-UpdateError 'uvの自己更新: Ctrl+Cで中断できます。'
        $selfUpdate = {
            param($attempt,$uv,$arguments,$record)
            $record.MatchText = 'but the standalone installer was used to install uv to'
            $status = & $attempt $uv $arguments
            if ($status -in 0,124,130,-1073741510) { return $status }
            if (-not $record.Matched) { return 1 }
            $record.WriteLine('uvの導入先の記録を現在の実体へそろえるため、公式installerで再導入する。')
            & $attempt 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'update_uv.ps1'), $uv)
        }
        $null = Invoke-UpdateStage 'uvの自己更新' continue 300 $UvPath @('self', 'update') -Run $selfUpdate
        Write-UpdateError '管理Pythonの更新: uvキャッシュの整理中は待つ場合があります。Ctrl+Cで中断できます。'
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
        if ($unrestorable.Count -and -not (Test-PythonPatchPending)) {
            Remove-Item -LiteralPath $state -ErrorAction SilentlyContinue
        } elseif ($unrestorable.Count) {
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
    # 前回の更新が始めたuvの整理がキャッシュを排他している間も待たないよう、キャッシュを使わずに解決する。
    Write-UpdateError '管理Pythonの検索: Ctrl+Cで中断できます。'
    $findCode = Invoke-UpdateStage '管理Pythonの導入' abort 60 $UvPath @('python', 'find', '--no-cache', '3.15') -Probe
    if ($findCode -ne 0) {
        Write-UpdateError '管理Pythonの導入: uvキャッシュの整理中は待つ場合があります。Ctrl+Cで中断できます。'
        try { [void](Invoke-UpdateStage '管理Pythonの導入' abort 900 $UvPath @('python', 'install', '3.15')) }
        catch [System.TimeoutException] { throw }
        catch { [void](Invoke-UpdateStage '管理Pythonの導入' abort 900 $UvPath @('python', 'install', '3.15')) }
        Write-UpdateError '管理Pythonの検索: Ctrl+Cで中断できます。'
        $findCode = Invoke-UpdateStage '管理Pythonの導入' abort 60 $UvPath @('python', 'find', '--no-cache', '3.15') -Probe
        if ($findCode -ne 0) { throw '使える3.15がありません。uv python install 3.15を実行してください。' }
    }
    if (@($script:bootstrapFailures | Where-Object { $_.title -eq '管理Pythonの更新' }).Count -and
        $script:lastStageOutput -match '3\.15\.\d+(a|b|rc)\d+') {
        Write-UpdateError ("残った管理Pythonはpre-releaseです: $($script:lastStageOutput)。次回起動時に更新を再試行します。")
    }
} catch {
    Write-UpdateError ("$_")
    exit 1
} finally {
    if ($script:stageInterrupted) {
        # -Fileで起動した専用ホストは、停止したパイプラインのexitだけでは0を返す場合がある。
        if (-not $MyInvocation.Line) { $Host.SetShouldExit(130) }
        exit 130
    }
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
