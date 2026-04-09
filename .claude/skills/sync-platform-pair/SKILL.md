---
name: sync-platform-pair
description: dotfiles リポジトリの Linux/Windows ペア ファイル (update-dotfiles と .cmd 版、run_onchange_after_pytools.sh.tmpl と -windows.ps1.tmpl 版、supply-chain-npm の sh 版と ps1 版など) を編集するときに使う。片方だけ更新するミスを避けるため、ペアの対応関係と PowerShell 側の書き方の注意点をまとめている。「ペアファイル」「Linux と Windows 両対応」などのキーワードで自動トリガー可
---

# Linux/Windows ペアファイル編集支援

## いつ使うか

`~/dotfiles/` で以下のどちらかのファイルを編集するとき、対になるファイルも同時に更新する必要があるか確認する。片方だけ変更するとバグが発生しやすい。

## ペアファイル一覧

| Linux/macOS 側 | Windows 側 | 用途 |
| --- | --- | --- |
| `bin/executable_update-dotfiles` | `bin/executable_update-dotfiles.cmd` | dotfiles 更新コマンド |
| `.chezmoi-source/run_onchange_after_pytools.sh.tmpl` | `.chezmoi-source/run_onchange_after_pytools-windows.ps1.tmpl` | pytools の自動再インストール |
| `.chezmoi-source/run_after_supply-chain-npm.sh.tmpl` | `.chezmoi-source/run_after_supply-chain-npm-windows.ps1.tmpl` | npm サプライ チェーン保護の適用 |
| `share/claude_settings_json_managed.posix.json` | `share/claude_settings_json_managed.win32.json` | Claude Code のフック定義 (OS 別オーバーライド) |
| `install-claude.sh` | `install-claude.ps1` | agent-basics ルールのリモート インストーラー |
| `install.sh` | (README の `winget` コマンド) | dotfiles 本体インストーラー |

新しいペアを追加する場合は本リストと `CLAUDE.md` の「プラットフォーム対応ファイル」節を同時に更新する。

## 一般的な注意点

- 対応関係を崩す変更 （片方のパスを変えるなど） は避ける
- 追加・削除は両側で同時に行う
- プラットフォーム依存の部分 （パス区切り、改行、環境変数の書き方） 以外は意味的に同じになるよう統一する
- 新しいペアを追加したら `.chezmoiignore` で対応ファイルを除外する （chezmoiがOSごとに適切なファイルをデプロイするため）

## PowerShell / `.ps1.tmpl` 側の必須作法

本リポジトリのPowerShellスクリプトはWindows PowerShell 5.1互換を保つ。以下を必ず遵守する。

- 改行はCRLF — `.gitattributes` で `*.ps1.tmpl` を `eol=crlf` 指定済み。PowerShell 5.1はLF改行のみだと構文解析に失敗する
  - Claude CodeのWriteツールは常にLFで書き込むため、エディター保存後にgit側でCRLFに正規化される前提
  - このフックの `claude_hook_check_ps1_eol` がLFのみのペイロード書き込みを検出してブロックする
- ファイル先頭で厳格モード — 全スクリプト冒頭に以下の2行を記述する。

  ```powershell
  Set-StrictMode -Version Latest
  $ErrorActionPreference = 'Stop'
  ```

- UTF-8の明示 — ファイル入出力では必ずエンコーディングを指定する。既定のShift-JISでは日本語が正しく扱えない。

  ```powershell
  Get-Content -Encoding UTF8 $path
  $content | Set-Content -Encoding UTF8 $path
  ```

  加えて、BOMなしUTF-8で書き出す場合は `System.Text.UTF8Encoding` のインスタンスを使う。
  既存 `install-claude.ps1` の `$script:utf8NoBom` を参照。

- パス区切り — ハードコードを避け、`Join-Path` や `[IO.Path]::Combine` を使う

- 環境変数 — `$HOME` / `$env:USERPROFILE` のどちらを使うかをスクリプト内で統一し、両者を併用しない

## Bash / `.sh.tmpl` 側の対応

- `set -eux` でエラー/未定義変数/コマンド表示を有効化 （既存スクリプトに合わせる）
- パスは必ずダブル クォートで囲む （スペース対応）
- Windows版と同じ処理を別の記法で書いているだけの場合、両方に同一のコメントを付けて対応関係を示す

## 変更フロー

1. 変更対象のペアを特定する（上記一覧または `CLAUDE.md` を参照）
2. 意味的な変更を両方に適用する
3. プラットフォーム固有の書き方の違いのみ確認する
4. 可能であれば両方を実行して動作確認する （Linuxでのみ実行可能な環境では最低限syntax check）
5. `make test` がgreenであることを確認する
6. コミット メッセージにペアを両方記載する
