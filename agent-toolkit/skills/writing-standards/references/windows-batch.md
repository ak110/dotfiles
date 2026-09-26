# Windowsバッチファイル記述スタイル

本書はcmd.exeが実行するバッチファイル（`.cmd`・`.bat`）の記述スタイル基準を定める。

- エンコーディング
  - CP932で記述する
    - cmd.exeのバッチファイルパーサーはシステムACP（日本語WindowsではCP932）で動作する
    - `chcp 65001`が変更するのはコンソールI/Oのコードページであり、パーサーはシステムACPのまま動作する
  - Claude Codeの書込ツールで非UTF-8とCRLFのファイルを扱う共通の手段は`encoding.md`「書込ツールの改行・BOM保全」に従う。CP932ファイルは次の手順で扱う
    - `git show`もCP932バイト列をそのまま出力するため回避策にならない
    - iconv経由で操作する
      - 読み取り: `iconv -f cp932 -t utf-8 file.cmd`
      - 編集: UTF-8に変換 → Edit/Writeで編集 → `iconv -f utf-8 -t cp932`でCP932に戻す
      - 新規作成: UTF-8で記述 → `iconv -f utf-8 -t cp932`で変換
    - Pythonでバイナリ書き換えする選択肢もある（iconv往復より手順が短くCRLFも自然に維持できる）
      - 単純置換・行追加・日本語コメント変更のいずれにも対応できる
      - 雛形: `from pathlib import Path; p=Path('file.cmd'); data=p.read_bytes(); p.write_bytes(data.replace(old, new))`
      - `old`/`new`は`'文字列'.encode('cp932')`で生成し、行単位の置換では末尾の`b'\r\n'`を含める
      - 行追加は「直前行＋`\r\n`＋新規行＋`\r\n`」のパターンで`old`の末尾に`b'\r\n'`を含めて置換するとCRLFを維持できる
- 改行コード
  - CRLFが必須。`.gitattributes`で`*.cmd text eol=crlf`を設定する
  - Writeで新規作成した場合は、iconv変換後にBashで`sed -i 's/$/\r/' file.cmd`を実行してCRLFに変換する
- 基本構造
  - `@echo off`でコマンドエコーを無効化する
  - `setlocal`/`endlocal`で環境変数のスコープを制御する
  - `exit /b <code>`でスクリプトの終了コードを明示する
- 変数展開
  - 遅延展開が必要な場面では`setlocal enabledelayedexpansion`を使う
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。cmd.exeでは動的な分岐を`if`・`goto`など固定候補への分岐で表す（努力目標）
- 推奨事項
  - 新規スクリプトではPowerShellの利用を検討する（UTF-8を標準で扱うことができ、構造化された制御構文・例外処理を備えるため）
  - `.cmd`はレガシー互換やPowerShellを利用できない環境向けに限定する
