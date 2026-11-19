# ssh-unelevatedが接続先のWindowsで、sshのセッションのtoken（UACで昇格している）のまま実行する。
# ログオン中のユーザーの対話セッションで非昇格（RunLevel Limited）の予定タスクを一時登録してrunner.ps1を実行し、
# 標準出力へスクリプトの出力をそのまま書いた後、`SSH_UNELEVATED_RESULT:<RunId>`で始まる行へ結果のJSONを書く。
param(
    [Parameter(Mandatory)][string] $RunId,
    [Parameter(Mandatory)][int] $TimeoutSeconds,
    [Parameter(Mandatory)][string] $ScriptBase64,
    [Parameter(Mandatory)][string] $RunnerBase64
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$stdout = [Console]::OpenStandardOutput()
# 中断された実行の残骸を後の実行が見分けられるよう、名前へ片付けてよい時刻（UNIX秒）を入れる。
# その時刻には、この実行は上限までの待機と後始末を終えているか、既に失われている。
$namePattern = '^ssh-unelevated-(\d+)-[0-9a-f]+$'
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$name = 'ssh-unelevated-{0}-{1}' -f ($now + $TimeoutSeconds + 120), $RunId
$workDir = [IO.Path]::Combine([IO.Path]::GetTempPath(), $name)

function Write-Result([hashtable] $Result) {
    $bytes = [Text.Encoding]::UTF8.GetBytes("`nSSH_UNELEVATED_RESULT:$RunId " + (ConvertTo-Json $Result -Compress) + "`n")
    $stdout.Write($bytes, 0, $bytes.Length)
    $stdout.Flush()
}

function Copy-Output {
    $path = Join-Path $workDir 'output.txt'
    if (-not (Test-Path -LiteralPath $path)) {
        return
    }
    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        # Windows PowerShellのOut-Fileが付けるBOMを除く。
        $head = New-Object byte[] 3
        $count = $stream.Read($head, 0, 3)
        if (-not ($count -eq 3 -and $head[0] -eq 0xEF -and $head[1] -eq 0xBB -and $head[2] -eq 0xBF)) {
            $stdout.Write($head, 0, $count)
        }
        $stream.CopyTo($stdout)
        $stdout.Flush()
    } finally {
        $stream.Dispose()
    }
}

function Get-RunProcessId([string] $Dir) {
    # タスクが起動したプロセス（コマンド行に作業ディレクトリを含む）と、その子孫のプロセスIDを返す。
    # プロセスIDの再利用で無関係のプロセスを含めないよう、親より後に作られた子だけをたどる。
    $all = @(Get-CimInstance Win32_Process)
    $found = @{}
    foreach ($p in $all) {
        if ($p.CommandLine -and $p.CommandLine.Contains($Dir)) {
            $found[$p.ProcessId] = $p
        }
    }
    do {
        $added = $false
        foreach ($p in $all) {
            $parent = $found[$p.ParentProcessId]
            if ($parent -and -not $found.ContainsKey($p.ProcessId) -and $p.CreationDate -ge $parent.CreationDate) {
                $found[$p.ProcessId] = $p
                $added = $true
            }
        }
    } while ($added)
    return $found.Keys
}

function Clear-RunProcess([string] $Name) {
    # タスクの停止だけでは子孫のプロセスが残りうるため、残ったプロセスも終了させる。
    $task = Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $Name -TaskPath '\'
    }
    $ids = @(Get-RunProcessId ([IO.Path]::Combine([IO.Path]::GetTempPath(), $Name)))
    if ($ids.Count -gt 0) {
        Stop-Process -Id $ids -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $ids -Timeout 10 -ErrorAction SilentlyContinue
    }
}

function Clear-Run([string] $Name, [bool] $ScanProcesses) {
    # プロセスの一覧の取得は数秒かかるため、タスクが動いている場合と、中断された実行の残骸を片付ける場合に限る。
    # 正常に終えたタスクは、runner.ps1がexit.txtを書いて終了するまで待ってある。
    $task = Get-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction SilentlyContinue
    if ($ScanProcesses -or ($task -and $task.State -eq 'Running')) {
        Clear-RunProcess $Name
    }
    if ($task) {
        Unregister-ScheduledTask -TaskName $Name -TaskPath '\' -Confirm:$false
    }
    $dir = [IO.Path]::Combine([IO.Path]::GetTempPath(), $Name)
    if (Test-Path -LiteralPath $dir) {
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
}

function Clear-StaleRun {
    $names = @(
        Get-ScheduledTask -TaskPath '\' -TaskName 'ssh-unelevated-*' -ErrorAction SilentlyContinue | ForEach-Object { $_.TaskName }
        Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) -Directory -Filter 'ssh-unelevated-*' | ForEach-Object { $_.Name }
    )
    foreach ($staleName in ($names | Sort-Object -Unique)) {
        if ($staleName -match $namePattern -and [long] $Matches[1] -lt $now) {
            Clear-Run $staleName $true
        }
    }
}

function Test-InteractiveLogon([string] $Sid) {
    # 対話ログオンの有無を、そのユーザーのexplorer.exeの有無で判定する。
    foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'")) {
        if ((Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid).Sid -eq $Sid) {
            return $true
        }
    }
    return $false
}

function Wait-Run {
    $exitFile = Join-Path $workDir 'exit.txt'
    $startedAt = Get-Date
    $deadline = $startedAt.AddSeconds($TimeoutSeconds)
    Start-ScheduledTask -TaskName $name -TaskPath '\'
    $started = $false
    while ($true) {
        if (Test-Path -LiteralPath $exitFile) {
            # 作業ディレクトリを消せるよう、タスクのプロセスが終わるのを待つ。
            $until = (Get-Date).AddSeconds(10)
            while ((Get-ScheduledTask -TaskName $name -TaskPath '\').State -eq 'Running' -and (Get-Date) -lt $until) {
                Start-Sleep -Milliseconds 200
            }
            return @{ status = 'completed'; exit_code = [long] (Get-Content -LiteralPath $exitFile -Raw).Trim() }
        }
        $state = (Get-ScheduledTask -TaskName $name -TaskPath '\').State
        $info = Get-ScheduledTaskInfo -TaskName $name -TaskPath '\'
        if ($state -eq 'Running') {
            $started = $true
        } elseif ($started -or $info.LastRunTime -ge $startedAt.AddSeconds(-5)) {
            # タスクの終了とexit.txtの出現の前後を吸収してから判定する。
            Start-Sleep -Seconds 1
            if (-not (Test-Path -LiteralPath $exitFile)) {
                return @{ status = 'no_result'; task_result = [long] $info.LastTaskResult }
            }
            continue
        } elseif ((Get-Date) -gt $startedAt.AddSeconds(30)) {
            return @{ status = 'not_started'; task_result = [long] $info.LastTaskResult }
        }
        if ((Get-Date) -gt $deadline) {
            Clear-RunProcess $name
            return @{ status = 'timeout' }
        }
        Start-Sleep -Milliseconds 500
    }
}

try {
    Clear-StaleRun
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (Test-InteractiveLogon $identity.User.Value)) {
        Write-Result @{ status = 'not_logged_on'; user = $identity.Name }
        return
    }
    New-Item -ItemType Directory -Path $workDir | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $workDir 'script.ps1'), [Convert]::FromBase64String($ScriptBase64))
    $runner = Join-Path $workDir 'runner.ps1'
    [IO.File]::WriteAllBytes($runner, [Convert]::FromBase64String($RunnerBase64))
    # conhost.exe --headlessで、ユーザーの画面にコンソールの窓を出さない。
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $env:USERPROFILE `
        -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`""
    # sshのセッションでは$env:USERDOMAINと$env:USERNAMEの組をSIDへ解決できないため、tokenの名前を使う。
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds ($TimeoutSeconds + 60)) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $action -Principal $principal -Settings $settings | Out-Null
    $result = Wait-Run
    Copy-Output
    Write-Result $result
} catch {
    Write-Result @{ status = 'error'; message = "$_" }
} finally {
    try {
        Clear-Run $name $false
    } catch {
        [Console]::Error.WriteLine("ssh-unelevated: 接続先の後始末に失敗した（次回の実行が片付ける）: $_")
    }
}
