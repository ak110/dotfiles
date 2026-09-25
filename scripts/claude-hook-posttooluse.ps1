# 互換入口: 個人用PostToolUseの登録を撤去した後も、撤去前の設定を読み込んで稼働中のセッションが
# このパスを起動するため、何もせず正常終了する。
# 撤去条件: 撤去前の設定を読み込んだWindowsのセッションが全て終了したことを確認できた後に削除する。
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

exit 0
