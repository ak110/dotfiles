# 運用機能: Windowsの電源設定

## Windowsの電源設定の最適化（optimize-power-settings）

`optimize-power-settings`コマンドはWindows専用で、高速スタートアップとUSB selective suspendをまとめて無効化する。

- 管理者権限が必要で、未昇格時は`Start-Process -Verb RunAs`でUAC自昇格再起動する（子プロセスはEnter待ち）
- 適用は冪等性を保ち、現在値が望ましい値の場合は変更せず「変更なし」と表示する
