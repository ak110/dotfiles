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
    $cause = $null
    $summary = "exit=$Code"
    $continuation = $false
    $visible = foreach ($line in $lines) {
        if (-not $line -or $line -cmatch $routine) {
            $continuation = $false
        } elseif ($line -cmatch $actionable -or ($continuation -and $line.StartsWith(' '))) {
            $line
            $continuation = $true
        } else {
            $continuation = $false
            $summary = $line
            if ($line -like '*cause:*' -or $line -like 'error:*') { $cause = $line }
        }
    }
    if ($Code -ne 0) {
        if ($cause) { $summary = $cause }
        Write-UpdateError ("$summary (詳細: $log)")
    }
    foreach ($line in $visible) { Write-UpdateError $line }
}
