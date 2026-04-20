---
name: sync-platform-pair
description: >
  dotfilesリポジトリのLinux/Windowsペアファイルを編集するときに使う。
  ペアファイルの例は`executable_update-dotfiles`と`.cmd`版、`run_after_post-apply.sh.tmpl`と`-windows.ps1.tmpl`版など。
  他の例は`install.sh`と`install.ps1`、`install-claude.sh`と`install-claude.ps1`など。
  「ペアファイル」「LinuxとWindows両対応」などのキーワードでも起動する。
user-invocable: true
---

# Linux/Windowsペアファイル編集支援

## いつ使うか

`~/dotfiles/` でLinux/Windowsのペアファイルのいずれかを編集するときに使う。
片方だけ変更すると配布経路の一方が不整合になるため、対応するファイルの更新を忘れやすい作業である。

## ペアファイル一覧の参照

ペアファイルの対応関係は`docs/development/development.md`の「プラットフォーム対応ファイル」節を参照する。
編集対象のファイルがペアの片方に該当するかは、まず当該節で確認する。

## 片方のみ編集するリスク

以下の事例が発生しやすい。作業着手時に必ず両側の対応関係を確認する。

- Windows側の`.cmd`や`.ps1`を更新し忘れ、Linux側だけで挙動が変わる
- `install.sh`のオプション追加後、`install.ps1`に同じオプションを追加し忘れる
- `run_after_post-apply.sh.tmpl`に処理を追加しても、`run_after_post-apply-windows.ps1.tmpl`側が空のままで挙動が非対称になる
- `share/claude_settings_json_managed.posix.json`と`.win32.json`のマッチャー追加が片方だけで止まる

## 新規ペアの追加

新しくペアを追加する場合、更新先は`docs/development/development.md`の「プラットフォーム対応ファイル」節に限定する。
`CLAUDE.md`は`development.md`への参照導線そのものを変更する場合のみ更新対象とする。

新規ペアの種類によっては`.chezmoiignore`への除外エントリ追加も必要になる（chezmoiがOSごとに適切なファイルをデプロイするため）。

## 一般的な注意点

- 対応関係を崩す変更（片方のパスを変えるなど）は避ける
- 追加・削除は両側で同時に行う
- プラットフォーム依存の部分（パス区切り、改行、環境変数の書き方）以外は意味的に同じになるよう統一する

## PowerShell / `.ps1.tmpl` 側の必須作法

本リポジトリのPowerShellスクリプトはWindows PowerShell 5.1互換を保つ。以下を必ず遵守する。

- 改行はCRLF — `.gitattributes` で `*.ps1.tmpl` を `eol=crlf` 指定済み。PowerShell 5.1はLF改行のみだと構文解析に失敗する
  - Claude CodeのWriteツールは常にLFで書き込むため、エディター保存後にgit側でCRLFに正規化される前提
  - このフックの `claude_hook_check_ps1_eol` がLFのみのペイロード書き込みを検出してブロックする
- ファイル先頭で厳格モード — 全スクリプト冒頭に以下の2行を記述する

  ```powershell
  Set-StrictMode -Version Latest
  $ErrorActionPreference = 'Stop'
  ```

- UTF-8の明示 — ファイル入出力では必ずエンコーディングを指定する。既定のShift-JISでは日本語が正しく扱えない

  ```powershell
  Get-Content -Encoding UTF8 $path
  $content | Set-Content -Encoding UTF8 $path
  ```

  加えて、BOMなしUTF-8で書き出す場合は `System.Text.UTF8Encoding` のインスタンスを使う。
  既存 `install-claude.ps1` の `$script:utf8NoBom` を参照

- パス区切り — ハードコードを避け、`Join-Path` や `[IO.Path]::Combine` を使う

- 環境変数 — `$HOME` / `$env:USERPROFILE` のどちらを使うかをスクリプト内で統一し、両者を併用しない

## Bash / `.sh.tmpl` 側の対応

- `set -eux` でエラー/未定義変数/コマンド表示を有効化（既存スクリプトに合わせる）
- パスは必ずダブルクォートで囲む（スペース対応）
- Windows版と同じ処理を別の記法で書いているだけの場合、両方に同一のコメントを付けて対応関係を示す

## 変更フロー

1. 変更対象のペアを`docs/development/development.md`の「プラットフォーム対応ファイル」節で特定する
2. 意味的な変更を両方に適用する
3. プラットフォーム固有の書き方の違いのみ確認する
4. 可能であれば両方を実行して動作確認する（Linuxでのみ実行可能な環境では最低限syntax check）
5. `uv run pyfltr run-for-agent` がgreenであることを確認する
6. コミットメッセージにペアを両方記載する
