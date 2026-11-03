Set-StrictMode -Version Latest
if (-not (Test-Path "$env:USERPROFILE\.claude\rules\agent-toolkit\01-agent.md")) {
  Write-Output "::error::agent-toolkit/01-agent.mdがありません。chezmoi適用とpost-applyのログを確認し、修正後にRe-run failed jobsで再実行してください。"
  exit 1
}
if (-not (Test-Path "$env:USERPROFILE\.ssh\authorized_keys")) {
  Write-Output "::error::.ssh/authorized_keysがありません。chezmoi適用ログと配布元を確認し、修正後にRe-run failed jobsで再実行してください。"
  exit 1
}
$envKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
if ($null -eq $envKey) {
  Write-Output "::error::ユーザーのEnvironmentレジストリキーがありません。post-applyの環境変数設定ログを確認し、修正後にRe-run failed jobsで再実行してください。"
  exit 1
}
try {
  $pathValue = $envKey.GetValue('Path', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $pathKind = $envKey.GetValueKind('Path')
} finally {
  $envKey.Close()
}
if ($pathValue -notlike '*%USERPROFILE%\dotfiles\bin*') {
  Write-Output "::error::ユーザーのPathに%USERPROFILE%\dotfiles\binがありません。post-applyのPATH設定ログを確認し、修正後にRe-run failed jobsで再実行してください。"
  exit 1
}
if ($pathKind -ne [Microsoft.Win32.RegistryValueKind]::ExpandString) {
  Write-Output "::error::ユーザーのPathがExpandString型ではありません。post-applyのPATH設定ログを確認し、修正後にRe-run failed jobsで再実行してください。"
  exit 1
}
