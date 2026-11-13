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

function Invoke-PytoolsMediaRemoteStop {
    param([object[]]$Processes)
    foreach ($listed in $Processes) {
        if (-not $listed.IsMediaRemote) { throw '再起動できないプロセスは停止しません。' }
        $current = Get-Process -Id $listed.ProcessId -ErrorAction SilentlyContinue
        if (-not $current) { continue }
        try {
            # ハンドルを保持してから世代を比べ、PID再利用で別のプロセスを停止しない。
            [void]$current.Handle
            if ($current.HasExited) { continue }
            # CIMの生成日時はマイクロ秒精度なので、100nsの末尾桁をそろえる。
            $started = $current.StartTime.ToUniversalTime()
            $started = $started.AddTicks(-($started.Ticks % 10))
            if ($started -ne $listed.CreationDate.ToUniversalTime()) {
                throw "PID=$($listed.ProcessId)の所有者が変わったため更新を見送ります。"
            }
            $current.Kill()
            if (-not $current.WaitForExit(5000)) { throw "PID=$($listed.ProcessId)が停止しません。" }
        } catch {
            # 親の停止で列挙済みの子も先に消える。消滅は停止の成功である。
            if (-not $current.HasExited) { throw }
        } finally { $current.Dispose() }
    }
}
