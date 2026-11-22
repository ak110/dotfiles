# ssh-unelevatedが接続先のWindowsで、sshのセッションのtoken（UACで昇格している）のまま実行する。
# ログオン中のユーザーの対話セッションで非昇格（RunLevel Limited）の予定タスクを一時登録してrunner.ps1を実行し、
# 標準出力へスクリプトの出力をそのまま書いた後、`SSH_UNELEVATED_RESULT:<RunId>`で始まる行へ結果のJSONを書く。
param(
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{16}$')][string] $RunId,
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
# 名前を指定したタスクの照会と操作は、タスクスケジューラーのCOMで名前から直接引いて行う。
# Get-ScheduledTaskは名前を指定してもフォルダーのタスクを列挙し、列挙中に別のタスクが消えると0x80070002で失敗する。
# 同時に動く別の実行がその実行のタスクを消した時に、待っている実行の照会が失敗していた（2026年11月19日、sthenoで観測）。
$taskFolder = $null
$owner = $null
$taskStateQueued = 2
$taskStateRunning = 4

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

function Get-RunTask([string] $Name) {
    try {
        return $taskFolder.GetTask($Name)
    } catch [IO.FileNotFoundException] {
        return $null
    }
}

function Clear-RunProcess([string] $Name) {
    # タスクの停止だけでは子孫のプロセスが残りうるため、残ったプロセスも終了させる。
    $task = Get-RunTask $Name
    if ($task -and $task.State -in @($taskStateQueued, $taskStateRunning)) {
        $task.Stop(0)
    }
    $ids = @(Get-RunProcessId ([IO.Path]::Combine([IO.Path]::GetTempPath(), $Name)))
    if ($ids.Count -gt 0) {
        Stop-Process -Id $ids -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $ids -Timeout 10 -ErrorAction SilentlyContinue
    }
}

function Clear-Run([string] $Name) {
    # この要求のタスクがまだ動いていれば、その子孫も終了させる。
    # 正常に終えたタスクは、runner.ps1がexit.txtを書いて終了するまで待ってある。
    $task = Get-RunTask $Name
    if ($task -and $task.State -in @($taskStateQueued, $taskStateRunning)) {
        Clear-RunProcess $Name
    }
    if ($task) {
        $taskFolder.DeleteTask($Name, 0)
    }
    $dir = [IO.Path]::Combine([IO.Path]::GetTempPath(), $Name)
    if (Test-Path -LiteralPath $dir) {
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
}

function Clear-StaleRun([string] $Parent) {
    # タスクの列挙は同時に別のタスクが消えると失敗しうるが、取りこぼした残骸は次回以降の実行が片付ける。
    $names = @(
        Get-ScheduledTask -TaskPath '\' -TaskName 'ssh-unelevated-*' -ErrorAction SilentlyContinue | ForEach-Object { $_.TaskName }
        Get-ChildItem -LiteralPath $Parent -Directory -Filter 'ssh-unelevated-*' | ForEach-Object { $_.Name }
    )
    foreach ($staleName in ($names | Sort-Object -Unique)) {
        if ($staleName -notmatch $namePattern -or [long] $Matches[1] -ge $now) {
            continue
        }
        $directory = Join-Path $Parent $staleName
        $claimPath = Join-Path $directory 'owner.lock'
        try {
            # 時刻だけでは稼働中の別要求を識別できない。所有者が閉じたロックを取れた場合だけ候補にする。
            $claim = [IO.File]::Open($claimPath, 'Open', 'ReadWrite', 'None')
        } catch [IO.IOException], [UnauthorizedAccessException] {
            continue
        }
        try {
            try {
                $task = Get-RunTask $staleName
                if (($task -and $task.State -in @($taskStateQueued, $taskStateRunning)) -or
                    @(Get-RunProcessId $directory).Count -gt 0) {
                    continue
                }
            } finally {
                # Windowsでは開いたロックがあるディレクトリを削除できない。
                $claim.Dispose()
            }
            Clear-Run $staleName
        } catch {
            # 同じ孤児を別の接続も片付ける場合がある。現在の要求は続ける。
            [Console]::Error.WriteLine("ssh-unelevated: 以前の要求の後始末を完了できなかった: $_")
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
    $startFile = Join-Path $workDir 'started.txt'
    $reportedStart = $false
    $exitFile = Join-Path $workDir 'exit.txt'
    $startedAt = Get-Date
    $deadline = $startedAt.AddSeconds($TimeoutSeconds)
    (Get-RunTask $name).Run($null) | Out-Null
    $started = $false
    $checkingResult = $false
    while ($true) {
        if (-not $reportedStart -and (Test-Path -LiteralPath $startFile) -and
            (Get-Content -LiteralPath $startFile -Raw).Trim() -ceq $RunId) {
            $bytes = [Text.Encoding]::ASCII.GetBytes("SSH_UNELEVATED_STARTED:$RunId`n")
            $stdout.Write($bytes, 0, $bytes.Length)
            $stdout.Flush()
            $reportedStart = $true
        }
        $exitMatch = $null
        if ($reportedStart -and (Test-Path -LiteralPath $exitFile)) {
            $exitMatch = [regex]::Match((Get-Content -LiteralPath $exitFile -Raw).Trim(), "^$RunId (-?\d+)$")
        }
        if ($exitMatch -and $exitMatch.Success) {
            # 作業ディレクトリを消せるよう、タスクのプロセスが終わるのを待つ。
            $until = (Get-Date).AddSeconds(10)
            while ((Get-RunTask $name).State -eq $taskStateRunning -and (Get-Date) -lt $until) {
                Start-Sleep -Milliseconds 200
            }
            return @{ status = 'completed'; exit_code = [long] $exitMatch.Groups[1].Value }
        }
        $task = Get-RunTask $name
        # COMは結果の値を符号付きで返すため、HRESULTの表記（0x8007...）に合わせて符号なしへ戻す。
        $taskResult = ([long] $task.LastTaskResult) -band 4294967295
        if ($task.State -eq $taskStateRunning) {
            $started = $true
        } elseif ($started -or $task.LastRunTime -ge $startedAt.AddSeconds(-5)) {
            # タスクの終了とexit.txtの出現の前後を吸収してから判定する。
            if ($checkingResult) {
                return @{ status = 'no_result'; task_result = $taskResult }
            }
            $checkingResult = $true
            Start-Sleep -Seconds 1
            continue
        } elseif ((Get-Date) -gt $startedAt.AddSeconds(30)) {
            return @{ status = 'not_started'; task_result = $taskResult }
        }
        if ((Get-Date) -gt $deadline) {
            Clear-RunProcess $name
            return @{ status = 'timeout' }
        }
        Start-Sleep -Milliseconds 500
    }
}

try {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    $taskFolder = $service.GetFolder('\')
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (Test-InteractiveLogon $identity.User.Value)) {
        Write-Result @{ status = 'not_logged_on'; user = $identity.Name }
        return
    }
    New-Item -ItemType Directory -Path $workDir | Out-Null
    $owner = [IO.File]::Open((Join-Path $workDir 'owner.lock'), 'CreateNew', 'ReadWrite', 'None')
    Clear-StaleRun ([IO.Path]::GetDirectoryName($workDir))
    [IO.File]::WriteAllBytes((Join-Path $workDir 'script.ps1'), [Convert]::FromBase64String($ScriptBase64))
    $runner = Join-Path $workDir 'runner.ps1'
    [IO.File]::WriteAllBytes($runner, [Convert]::FromBase64String($RunnerBase64))
    # conhost.exe --headlessで、ユーザーの画面にコンソールの窓を出さない。
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $env:USERPROFILE `
        -Argument "--headless powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runner`" -LeaveTaskJob -RunId $RunId"
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
        # 自分が作成した要求だけを片付ける。
        if ($owner) {
            $owner.Dispose()
            Clear-Run $name
        }
    } catch {
        [Console]::Error.WriteLine("ssh-unelevated: 接続先の後始末に失敗した（次回の実行が片付ける）: $_")
    }
}
