# Windowsバッチファイル記述スタイル

本書はcmd.exeが実行するバッチファイル（`.cmd`・`.bat`）の記述スタイル基準を定める。

- エンコーディング
  - CP932で記述する
    - cmd.exeのバッチファイルパーサーはシステムACP（日本語WindowsではCP932）で動作する
    - `chcp 65001`が変更するのはコンソールI/Oのコードページであり、パーサーはシステムACPのまま動作する
  - 非UTF-8とCRLFのファイルを読み書きする手段（iconvの往復、Pythonによるバイト列の置換、CRLFの新規ファイルの作成）は`encoding.md`「書込ツールの改行・BOM保全」に従う。CP932に固有の事実は次のとおり
    - `git show`もCP932バイト列をそのまま出力するため回避策にならない
    - iconvでは文字コード名に`cp932`を指定する（読み取りは`iconv -f cp932 -t utf-8 file.cmd`）
    - Pythonでバイト列を置換する場合、置換に使うバイト列は`'文字列'.encode('cp932')`で生成し、行単位の置換では行末の`b'\r\n'`を含める
- 改行コード
  - CRLFが必須。`.gitattributes`で`*.cmd text eol=crlf`を設定する
- 基本構造
  - `@echo off`でコマンドエコーを無効化する
  - `setlocal`/`endlocal`で環境変数のスコープを制御する
  - `exit /b <code>`でスクリプトの終了コードを明示する
- 変数展開
  - 遅延展開が必要な場面では`setlocal enabledelayedexpansion`を使う
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。cmd.exeでは動的な分岐を`if`・`goto`など固定候補への分岐で表す（努力目標）
- 推奨事項
  - 新規スクリプトはPowerShellを優先し、`.cmd`はレガシー互換やPowerShellを利用できない環境などで選ぶ（PowerShellはUTF-8を標準で扱うことができ、構造化された制御構文・例外処理を備えるため）
