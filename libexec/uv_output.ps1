function Write-UpdateError {
    param([string]$Message)
    if ([Console]::IsErrorRedirected) {
        $writer = [System.IO.StreamWriter]::new([Console]::OpenStandardError(), [System.Text.UTF8Encoding]::new($false))
        try { $writer.WriteLine($Message) } finally { $writer.Dispose() }
    } else { [Console]::Error.WriteLine($Message) }
}
function Write-UvOutput {
    param([string]$Stage, [int]$Code, [string]$OutputText)
    if (-not $OutputText -and $Code -eq 0) { return }
    $lines = $OutputText.TrimEnd([char[]]"`r`n") -split '\r?\n'
    $temp = $null
    $log = $null
    try {
        $directory = Join-Path $env:LOCALAPPDATA 'agent-toolkit'
        if ($Code -ne 0) { $Stage += '-failed' }
        $log = Join-Path $directory "uv-$Stage.log"
        $sensitive, $actionable, $patterns = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'uv_output_patterns.txt') -Encoding UTF8
        $routine = '^(' + ($patterns -join '|') + ')$'
        $saved = foreach ($line in $lines) {
            if ($line -match $sensitive -or $OutputText.Contains('PRIVATE KEY-----')) { '[REDACTED]' } else { $line }
        }
        [void][IO.Directory]::CreateDirectory($directory)
        $temp = Join-Path $directory ([IO.Path]::GetRandomFileName())
        [IO.File]::WriteAllLines($temp, [string[]](@("$(Get-Date -Format o) exit=$Code") + $saved), [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($log)) { [IO.File]::Replace($temp, $log, [NullString]::Value) }
        else { [IO.File]::Move($temp, $log) }
    } catch {
        Write-UpdateError ("明細を保存できません: ${log}: $_。書込権限と空き容量を確認してください。")
        Write-UpdateError $OutputText
        return
    } finally {
        if ($temp) { Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue }
    }
    $visible = @($lines | Where-Object { $_ -and $_ -cnotmatch $routine })
    if ($Code -ne 0) {
        $diagnosis = @($visible | Where-Object { $_ -cnotmatch $actionable })
        $summary = $diagnosis | Where-Object { $_ -like '*cause:*' -or $_ -like 'error:*' } | Select-Object -Last 1
        if (-not $summary) { $summary = $diagnosis | Select-Object -Last 1 }
        if (-not $summary) { $summary = "exit=$Code" }
        Write-UpdateError ("$summary (詳細: $log)")
        $visible = @($visible | Where-Object { $_ -cmatch $actionable })
    }
    foreach ($line in $visible) { Write-UpdateError $line }
}
