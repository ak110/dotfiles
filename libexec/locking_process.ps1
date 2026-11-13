# 更新対象の使用中検出をpost-applyとブートストラップで共用する。
if (-not (Test-Path Variable:mediaRemoteBin)) {
    $mediaRemoteBin = Join-Path $env:USERPROFILE '.local\bin\dotfiles-media-remote.exe'
}
$escapedMediaRemoteBin = [System.Management.Automation.WildcardPattern]::Escape($mediaRemoteBin)
function Test-MediaRemoteProcess {
    param([object] $Process)
    if ($Process.ExecutablePath -and
        $Process.ExecutablePath.Equals($mediaRemoteBin, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    if (-not $Process.CommandLine) { return $false }
    return ($Process.CommandLine -like "*$escapedMediaRemoteBin*") -or
        ($Process.CommandLine -like '*pytools.media_remote*')
}
function Get-PytoolsEnvLockingProcess {
    param([string]$Directory = $uvToolDir, [int[]]$ExcludeIds = @())
    $prefix = $Directory.TrimEnd('\') + '\'
    foreach ($process in Get-CimInstance Win32_Process -OperationTimeoutSec 30 -ErrorAction Stop) {
        if ($process.ProcessId -in $ExcludeIds) { continue }
        $isMediaRemote = Test-MediaRemoteProcess $process
        $isInToolEnv = $process.ExecutablePath -and
            $process.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
        if ($isInToolEnv -or $isMediaRemote) {
            $process | Select-Object *, @{Name = 'IsMediaRemote'; Expression = { $isMediaRemote } }
        }
    }
}
