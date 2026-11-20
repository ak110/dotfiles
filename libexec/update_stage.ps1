Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'update_stage_job.ps1')
. (Join-Path $PSScriptRoot 'uv_output.ps1')
function Invoke-UpdateStage {
    param([string]$Name, [string]$Classification, [int]$Seconds, [string]$Program, [string[]]$Arguments, [switch]$Probe,
        [scriptblock]$Start = { param($p,$a,$probe) Start-UpdateStageProcess $p $a $probe },
        [scriptblock]$Wait = { param($p,$ms) $p.Wait($ms) },
        [scriptblock]$Stop = { param($p) $p.Stop() },
        [scriptblock]$Output = { param($p) $p.ReadOutput() },
        [scriptblock]$Diagnostic = { param($p) $p.ReadDiagnostic() },
        [scriptblock]$ExitCode = { param($p) $p.ExitCode },
        [scriptblock]$Clock = { [DateTime]::UtcNow })
    $process = $null
    $code = 1
    try {
        $deadline = (& $Clock).AddSeconds($Seconds)
        $process = & $Start $Program $Arguments ([bool]$Probe)
        if (& $Wait $process ([Math]::Max(0, [int]($deadline - (& $Clock)).TotalMilliseconds))) { $code = & $ExitCode $process }
        else { $code = 124 }
    } catch [System.TimeoutException] { throw }
    catch { Write-UpdateError ("${Name}: $_") }
    finally {
        if ($null -ne $process) {
            try {
                # 起点の自然終了でもJob全体を回収する。所有不明はcontinueへ変換しない。
                try { & $Stop $process } catch { throw [System.TimeoutException]::new("${Name}のJobを回収できません: $_。この診断を添えて保守担当へ調査を依頼し、所有対象の終了を確認してからupdate-dotfilesを再実行してください。") }
                try {
                    $detail = [string](& $Output $process)
                    if ($Probe) {
                        $script:lastStageOutput = $detail.Trim()
                        $detail = [string](& $Diagnostic $process)
                    }
                } catch { throw [System.TimeoutException]::new("${Name}の出力を回収できません: $_。この診断を添えて保守担当へ調査を依頼し、出力取得の障害を解消してからupdate-dotfilesを再実行してください。") }
                Write-UvOutput $Name $code $detail
            } finally { $process.Dispose() }
        }
    }
    if ($code -ne 0) {
        $script:bootstrapFailures += @{title=$Name; detail="exit=$code"}
        Write-UpdateError ("${Name}に失敗しました（exit=$code）。既存版で更新を続け、次回起動時に再試行します。")
    }
    if ($Classification -eq 'abort' -and $code -ne 0 -and -not $Probe) { throw "${Name}を完了できません。通信の問題を解消してupdate-dotfilesを再実行してください。" }
    return $code
}
