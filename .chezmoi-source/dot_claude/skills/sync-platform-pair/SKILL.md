---
name: sync-platform-pair
description: dotfiles リポジトリの Linux/Windows ペア ファイル (update-dotfiles と .cmd 版、run_onchange_after_pytools.sh.tmpl と -windows.ps1.tmpl 版、supply-chain-npm の sh 版と ps1 版など) を編集するときに使う。片方だけ更新するミスを避けるため、ペアの対応関係と PowerShell 側の書き方の注意点をまとめている。「ペアファイル」「Linux と Windows 両対応」などのキーワードで自動トリガー可
---

# Linux/Windows ペアファイル編集支援

## いつ使うか

`~/dotfiles/` で以下のどちらかのファイルを編集するとき、**必ず対になるファイルも同時に更新する**必要があるか確認する。片方だけ変更するバグが起きやすい。

## ペアファイル一覧

| Linux/macOS 側 | Windows 側 | 用途 |
|---|---|---|
| `bin/executable_update-dotfiles` | `bin/executable_update-dotfiles.cmd` | dotfiles 更新コマンド |
| `.chezmoi-source/run_onchange_after_pytools.sh.tmpl` | `.chezmoi-source/run_onchange_after_pytools-windows.ps1.tmpl` | pytools の自動再インストール |
| `.chezmoi-source/run_after_supply-chain-npm.sh.tmpl` | `.chezmoi-source/run_after_supply-chain-npm-windows.ps1.tmpl` | npm サプライ チェーン保護の適用 |
| `share/claude_settings_json_managed.posix.json` | `share/claude_settings_json_managed.win32.json` | Claude Code のフック定義 (OS 別オーバーライド) |
| `install-claude.sh` | `install-claude.ps1` | agent-basics ルールのリモート インストーラー |
| `install.sh` | (README の `winget` コマンド) | dotfiles 本体インストーラー |

新しいペアを追加する場合は本リストと `CLAUDE.md` の「プラットフォーム対応ファイル」節を同時に更新する。

## 一般的な注意点

- 対応関係を崩す変更 (片方のパスを変えるなど) は避ける
- 追加・削除は両側で同時に行う
- プラットフォーム依存の部分 (パス区切り、改行、環境変数の書き方) 以外は**意味的に同じ**になるよう揃える
- 新しいペアを追加したら `.chezmoiignore` で相方を除外する (chezmoi が OS ごとに適切な方をデプロイできるように)

## PowerShell / `.ps1.tmpl` 側の必須作法

本リポジトリの PowerShell スクリプトは Windows PowerShell 5.1 互換を保つ。以下を必ず守る。

- **改行は CRLF** — `.gitattributes` で `*.ps1.tmpl` を `eol=crlf` 指定済み。PowerShell 5.1 は LF 改行だと構文解析が壊れる
  - Claude Code の Write ツールは常に LF で書き込むため、エディタで保存後に git 側で CRLF に正規化される想定
  - このフックの `claude_hook_check_ps1_eol` が LF のみのペイロード書き込みを検出してブロックする
- **ファイル先頭に厳格モード** — 全スクリプト冒頭に以下の 2 行を記述する。

  ```powershell
  Set-StrictMode -Version Latest
  $ErrorActionPreference = 'Stop'
  ```

- **UTF-8 の明示** — ファイル入出力では必ずエンコーディングを指定する。既定の Shift-JIS に頼ると日本語が文字化けする。

  ```powershell
  Get-Content -Encoding UTF8 $path
  $content | Set-Content -Encoding UTF8 $path
  ```

  加えて、BOM なし UTF-8 で書きたい場合は `System.Text.UTF8Encoding` のインスタンスを使う (既存 `install-claude.ps1` の `$script:utf8NoBom` を参照)。

- **パス区切り** — ハードコードは避け、`Join-Path` や `[IO.Path]::Combine` を使う

- **環境変数** — `$HOME` / `$env:USERPROFILE` のどちらを使うかをスクリプト内で統一する。混在させない

## Bash / `.sh.tmpl` 側の対応

- `set -eux` でエラー/未定義変数/コマンド表示を有効化 (既存スクリプトに倣う)
- パスは必ずダブル クォートで囲む (スペース対応)
- Windows 版と同じ処理を別の記法で書いているだけなら、両方に同一コメントを付けて対応関係を示す

## 変更フロー

1. 変更対象のペアを特定する (上記一覧または `CLAUDE.md` を参照)
2. **意味的な変更**を両方に適用する
3. プラットフォーム固有の書き方の違いだけ確認する
4. 可能であれば両方を実行して動作確認する (Linux でしか動かせない環境では最低限 syntax check)
5. `make test` が green であることを確認
6. コミット メッセージにペアを両方含める
