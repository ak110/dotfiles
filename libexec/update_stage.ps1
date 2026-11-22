Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'update_stage_job.ps1')
. (Join-Path $PSScriptRoot 'uv_output.ps1')
$script:stageInterrupted = $false
function Invoke-UpdateStage {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification = 'Start、Wait、Stop、Output、Diagnostic、ExitCodeは段を実行する$attemptの内側から呼ぶ。')]
    param([string]$Name, [string]$Classification, [int]$Seconds, [string]$Program, [string[]]$Arguments, [switch]$Probe,
        [scriptblock]$Start = { param($p,$a,$probe,$record) Start-UpdateStageProcess $p $a $probe $record },
        [scriptblock]$Wait = { param($p,$ms)
            # 長いWin32待機中にもPowerShellがCtrl+Cを処理できるようにする。
            $watch = [Diagnostics.Stopwatch]::StartNew()
            do {
                $slice = if ($ms -lt 0) { 100 } else { [Math]::Min(100, [Math]::Max(0, $ms - $watch.ElapsedMilliseconds)) }
                if ($p.Wait([int]$slice)) { return $true }
            } while ($ms -lt 0 -or $watch.ElapsedMilliseconds -lt $ms)
            return $false
        },
        [scriptblock]$Stop = { param($p) $p.Stop() },
        [scriptblock]$Output = { param($p) $p.ReadOutput() },
        [scriptblock]$Diagnostic = { param($p) $p.ReadDiagnostic() },
        [scriptblock]$ExitCode = { param($p) $p.ExitCode },
        [scriptblock]$Clock = { [DateTime]::UtcNow },
        [scriptblock]$Run = { param($attempt,$executable,$nativeArguments) & $attempt $executable $nativeArguments })
    $record = New-UvOutput
    $deadline = (& $Clock).AddSeconds($Seconds)
    $attempt = {
        param($exe,$argv)
        $process = $null
        try {
            if ($Seconds -and (& $Clock) -ge $deadline) { return 124 }
            $process = & $Start $exe $argv ([bool]$Probe) $record
            $limit = if ($Seconds) { [Math]::Max(0, [int]($deadline - (& $Clock)).TotalMilliseconds) } else { -1 }
            if (& $Wait $process $limit) {
                return & $ExitCode $process
            }
            return 124
        } catch [System.TimeoutException] { throw }
        catch [System.Management.Automation.PipelineStoppedException] { throw }
        catch { Write-UpdateError ("${Name}: $_"); return 1 }
        finally {
            if ($null -ne $process) {
                $cleanupFailure = $null
                try {
                    # 先にJobを回収し、パイプに残っている最後の診断もreaderが記録してから閉じる。
                    try { & $Stop $process } catch { throw [System.TimeoutException]::new("${Name}のJobを回収できません: $_。この診断を添えて保守担当へ調査を依頼し、所有対象の終了を確認してからupdate-dotfilesを再実行してください。") }
                    try {
                        $detail = [string](& $Output $process)
                        if ($Probe) {
                            $script:lastStageOutput = $detail.Trim()
                            [void](& $Diagnostic $process)
                        }
                    } catch { throw [System.TimeoutException]::new("${Name}の出力を回収できません: $_。この診断を添えて保守担当へ調査を依頼し、出力取得の障害を解消してからupdate-dotfilesを再実行してください。") }
                } catch {
                    $cleanupFailure = $_.Exception
                } finally {
                    try { $process.Dispose() } catch {
                        $detail = "$_"
                        if ($null -ne $cleanupFailure) { $detail = "${cleanupFailure}。追加の終了処理: $detail" }
                        else { $detail += '。更新を再実行する前に所有プロセスの終了を確認してください。' }
                        throw [System.TimeoutException]::new("${Name}の段の終了を確認できません: $detail")
                    }
                }
                if ($null -ne $cleanupFailure) { throw $cleanupFailure }
            }
        }
    }
    $code = 130
    try {
        $code = & $Run $attempt $Program $Arguments $record
        if ($code -eq -1073741510) { $code = 130 } # STATUS_CONTROL_C_EXIT
    } catch [System.Management.Automation.PipelineStoppedException] { throw }
    catch { $code = 1; throw }
    finally {
        $script:stageInterrupted = $code -eq 130
        Complete-UvOutput $Name $record $code
        if ($script:stageInterrupted) { exit 130 }
    }
    if ($Classification -eq 'return') { return $code }
    if ($code -ne 0) {
        $script:bootstrapFailures += @{title=$Name; detail="exit=$code"}
        Write-UpdateError ("${Name}に失敗しました（exit=$code）。既存版で更新を続け、次回起動時に再試行します。")
    }
    if ($Classification -eq 'abort' -and $code -ne 0 -and -not $Probe) { throw "${Name}を完了できません。通信の問題を解消してupdate-dotfilesを再実行してください。" }
    return $code
}

function Invoke-UpdateInstallStage {
    param(
        [string]$Name, [int]$TimeoutSec, [string]$Program, [string[]]$Arguments,
        [scriptblock]$RunStage = { param($n, $t, $p, $a) Invoke-UpdateStage $n abort $t $p $a }
    )
    # 所有Jobや出力の回収を確かめられない失敗では、次の導入を起動しない。
    try { return (& $RunStage $Name $TimeoutSec $Program $Arguments) }
    catch [System.TimeoutException] { throw }
    catch { return (& $RunStage $Name $TimeoutSec $Program $Arguments) }
}
