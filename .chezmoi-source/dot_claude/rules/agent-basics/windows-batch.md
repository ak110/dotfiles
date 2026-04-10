---
paths:
  - "**/*.cmd"
  - "**/*.bat"
  - "**/*.cmd.tmpl"
  - "**/*.bat.tmpl"
---

# Windowsバッチファイル記述スタイル

- エンコーディング
  - UTF-8で記述する（Claude Codeは内部的にUTF-8を前提としており、CP932ファイルはRead/Edit/Writeの全てで正常に扱えないため）
  - cmd.exeの日本語環境デフォルトコードページはCP932。日本語を含む出力（`echo`等）がある場合はスクリプト冒頭で`chcp 65001 >nul`を実行してコードページをUTF-8に切り替える
  - 既存のCP932ファイルをUTF-8に変換する: `iconv -f cp932 -t utf-8 file.cmd > file.cmd.tmp && mv file.cmd.tmp file.cmd`
- 改行コードとClaude Codeツールの注意点
  - CRLFが必須。`.gitattributes`で`*.cmd text eol=crlf`を設定する
  - EditツールはCRLFを透過的に維持する。既存ファイルの編集は問題なし
  - Writeツールは常にLFで書くため、新規作成後にBashで`sed -i 's/$/\r/' file.cmd`を実行してCRLFに変換する
- 基本構造
  - `@echo off`でコマンドエコーを無効化する
  - `setlocal`/`endlocal`で環境変数のスコープを制御する
  - `exit /b <code>`でスクリプトの終了コードを明示する
- 変数展開
  - 遅延展開が必要な場面では`setlocal enabledelayedexpansion`を使う
- セキュリティ上の危険パターン
  - ユーザー入力を含む文字列をそのまま実行しない
- 推奨事項
  - 新規スクリプトではPowerShellの使用を検討する（機能面・保守性の両面で優れるため）
  - `.cmd`はレガシー互換やPowerShellが使えない環境向けに限定する
