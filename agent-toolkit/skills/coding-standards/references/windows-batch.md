# Windowsバッチファイル記述スタイル

- エンコーディング
  - CP932で記述する。cmd.exeのバッチファイルパーサーはシステムACP（日本語WindowsではCP932）で動作する。
    `chcp 65001`はコンソールI/Oのコードページを変更するだけでパーサーには影響しない
  - Claude CodeのRead/Edit/WriteはUTF-8前提のため、CP932ファイルを直接扱えない。
    `git show`もCP932バイト列をそのまま出力するため回避策にならない。
    iconv経由で操作する
    - 読み取り: `iconv -f cp932 -t utf-8 file.cmd`
    - 編集: UTF-8に変換 → Edit/Writeで編集 → `iconv -f utf-8 -t cp932`でCP932に戻す
    - 新規作成: UTF-8で記述 → `iconv -f utf-8 -t cp932`で変換
- 改行コード
  - CRLFが必須。`.gitattributes`で`*.cmd text eol=crlf`を設定する
  - Writeツールは常にLFで書くため、新規作成後にBashで`sed -i 's/$/\r/' file.cmd`を実行してCRLFに変換する（iconv変換後に実施）
  - EditツールはCRLFを透過的に維持するが、CP932ファイルにはEditを直接使用できない
- 基本構造
  - `@echo off`でコマンドエコーを無効化する
  - `setlocal`/`endlocal`で環境変数のスコープを制御する
  - `exit /b <code>`でスクリプトの終了コードを明示する
- 変数展開
  - 遅延展開が必要な場面では`setlocal enabledelayedexpansion`を使う
- セキュリティ上の危険パターン
  - ユーザー入力を含む文字列をそのまま実行しない
- 推奨事項
  - 新規スクリプトではPowerShellの使用を検討する（機能面・保守性の両面で優れ、UTF-8を正しく処理できるため）
  - `.cmd`はレガシー互換やPowerShellが使えない環境向けに限定する
