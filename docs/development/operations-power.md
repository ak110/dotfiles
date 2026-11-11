# 運用機能: Windowsの電源設定

## Windowsの電源設定の最適化（optimize-power-settings）

`optimize-power-settings`コマンドはWindows専用で、高速スタートアップとUSB selective suspendをまとめて無効化する。

引数なしで設定処理を開始する。
自昇格した子は`-AutoElevated`を受け取る。
`--help`は使用方法を表示して終了コード0で終わり、未知引数は標準エラーへ診断を出力して非0で終わる。
どちらも管理者確認、自昇格、レジストリ・電源設定の操作へ進まない。
Windowsのスリープ抑制用`keep-awake`も、引数なしで本体を開始し、
`--help`と未知引数では同じ終了契約でAdd-Typeとスリープ抑制へ進まない。

- 高速スタートアップ無効化: `HiberbootEnabled=0`レジストリ書き込みと`powercfg /hibernate off`を実行する
- USB selective suspend無効化: 電源プラン層のAC・DC両系統と、per-device層（`SelectiveSuspendEnabled`と
  `MSPower_DeviceEnable.Enable`。`HKLM:\SYSTEM\CurrentControlSet\Enum\USB`配下）を全USBデバイスへ適用する
- 管理者権限が必要で、未昇格時は`Start-Process -Verb RunAs`でUAC自昇格再起動する（子プロセスはEnter待ち）
- 適用は冪等性を保ち、現在値が望ましい値の場合は変更せず「変更なし」と表示する
