param(
    [switch]$AutoElevated
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)] [string]$Description,
        [Parameter(Mandatory = $true)] [scriptblock]$ScriptBlock
    )
    & $ScriptBlock
    if ($LASTEXITCODE -ne 0) {
        throw ("{0} が失敗しました (exit={1})" -f $Description, $LASTEXITCODE)
    }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-SelfElevate {
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $PSCommandPath,
        '-AutoElevated'
    )
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments
}

function Disable-FastStartup {
    $regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
    $regName = 'HiberbootEnabled'
    $current = (Get-ItemProperty -Path $regPath -Name $regName -ErrorAction SilentlyContinue).$regName
    if ($current -eq 0) {
        Write-Host "HiberbootEnabled: 既に 0 のため変更なし"
    }
    else {
        Set-ItemProperty -Path $regPath -Name $regName -Value 0 -Type DWord
        Write-Host ("HiberbootEnabled: {0} -> 0 へ変更" -f $current)
    }

    Write-Host "powercfg /hibernate off を実行"
    Invoke-Native -Description 'powercfg /hibernate off' -ScriptBlock { & powercfg /hibernate off }
}

function Get-UsbSelectiveSuspendIndex {
    $subgroup = '2a737441-1930-4402-8d77-b2bebba308a3'
    $setting = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
    $output = & powercfg /query SCHEME_CURRENT $subgroup $setting 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw ("powercfg /query が失敗しました (exit={0})" -f $LASTEXITCODE)
    }
    $matchList = [regex]::Matches($output, '0x([0-9a-fA-F]+)')
    return $matchList | ForEach-Object { [Convert]::ToInt32($_.Groups[1].Value, 16) }
}

function Disable-UsbSelectiveSuspend {
    $subgroup = '2a737441-1930-4402-8d77-b2bebba308a3'
    $setting = '48e6b7a6-50f5-4782-a5d4-53bb8f07e226'
    $values = Get-UsbSelectiveSuspendIndex
    $allZero = ($values.Count -ge 2) -and ($values[-2] -eq 0) -and ($values[-1] -eq 0)
    if ($allZero) {
        Write-Host "USB selective suspend: AC・DC とも既に 0 のため変更なし"
    }
    else {
        Write-Host "USB selective suspend を AC・DC 両方 0 に設定"
        Invoke-Native -Description 'powercfg /setacvalueindex' -ScriptBlock {
            & powercfg /setacvalueindex SCHEME_CURRENT $subgroup $setting 0
        }
        Invoke-Native -Description 'powercfg /setdcvalueindex' -ScriptBlock {
            & powercfg /setdcvalueindex SCHEME_CURRENT $subgroup $setting 0
        }
        Invoke-Native -Description 'powercfg /SetActive' -ScriptBlock {
            & powercfg /SetActive SCHEME_CURRENT
        }
    }
}

function Show-CurrentState {
    Write-Host ""
    Write-Host "=== 適用後の状態 ==="

    $regPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
    $hiber = (Get-ItemProperty -Path $regPath -Name 'HiberbootEnabled' -ErrorAction SilentlyContinue).HiberbootEnabled
    Write-Host ("HiberbootEnabled = {0}" -f $hiber)

    Write-Host ""
    Write-Host "USB selective suspend (SCHEME_CURRENT):"
    Invoke-Native -Description 'powercfg /query (確認用)' -ScriptBlock {
        & powercfg /query SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226
    }
}

if (-not (Test-IsAdministrator)) {
    Write-Host "管理者権限が必要です。UAC で再起動します。"
    Invoke-SelfElevate
    exit 0
}

try {
    Write-Host "dotfiles-setup: Windows 電源設定を最適化します。"
    Write-Host ""

    Disable-FastStartup
    Write-Host ""
    Disable-UsbSelectiveSuspend
    Show-CurrentState

    Write-Host ""
    Write-Host "dotfiles-setup: 完了しました。"
}
finally {
    if ($AutoElevated) {
        Read-Host -Prompt 'Enter キーを押して終了'
    }
}
