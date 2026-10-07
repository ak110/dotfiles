# 互換入口: Windowsの個人用PreToolUseの実体は libexec/claude-hook-pretooluse.ps1 へ移した。
# 移動前の設定を読み込んで稼働中のセッションがこのパスを起動するため、新しい実体を呼び出してその終了コードを返す。
# 撤去条件: 移動前の設定を読み込んだWindowsのセッションが全て終了したことを確認できた後に削除する。
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

& (Join-Path (Split-Path -Parent $PSScriptRoot) 'libexec\claude-hook-pretooluse.ps1')
exit $LASTEXITCODE
