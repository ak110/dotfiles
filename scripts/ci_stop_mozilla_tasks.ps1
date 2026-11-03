Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskPath.StartsWith('\Mozilla\') })
if ($tasks.Count -eq 0) {
  Write-Output "TaskPathが\Mozilla\の予約タスクは0件である。"
  exit 0
}
foreach ($task in $tasks) {
  if ($task.State -eq 'Running') {
    Stop-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName
  }
  $disabled = Disable-ScheduledTask -TaskPath $task.TaskPath -TaskName $task.TaskName
  Write-Output "TaskPath=$($disabled.TaskPath) TaskName=$($disabled.TaskName) State=$($disabled.State)"
  if ("$($disabled.State)" -ne 'Disabled') {
    throw "予約タスクを無効化できなかった: $($task.TaskPath)$($task.TaskName)。Get-ScheduledTaskの状態と権限を確認し、runnerの問題としてGitHub Actionsの管理者へ報告してください。"
  }
}
