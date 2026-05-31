Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class NativeMethods {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@

$esSystemRequired = [Convert]::ToUInt32('00000001', 16)
$esDisplayRequired = [Convert]::ToUInt32('00000002', 16)
$esContinuous = [Convert]::ToUInt32('80000000', 16)

$awakeFlags = [uint32](
    $esContinuous -bor
    $esSystemRequired -bor
    $esDisplayRequired
)

$releaseFlags = $esContinuous

try {
    Write-Host 'keep-awake: スリープ抑制を開始しました。終了するには Ctrl+C を押してください。'

    $result = [NativeMethods]::SetThreadExecutionState($awakeFlags)

    if ($result -eq 0) {
        Write-Host 'keep-awake: SetThreadExecutionState の呼び出しに失敗しました。'
        exit 1
    }

    while ($true) {
        Start-Sleep -Seconds 30

        $result = [NativeMethods]::SetThreadExecutionState($awakeFlags)

        if ($result -eq 0) {
            Write-Host 'keep-awake: スリープ抑制の更新に失敗しました。'
        }
    }
}
finally {
    [void][NativeMethods]::SetThreadExecutionState($releaseFlags)
    Write-Host 'keep-awake: スリープ抑制を解除しました。'
}
