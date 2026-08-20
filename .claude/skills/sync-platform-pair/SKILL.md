---
name: sync-platform-pair
description: >
  Linux/Windowsペアファイル（`.sh`と`.cmd`、`.sh`と`.ps1`、
  `.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`など）を編集するときに使う。
  「ペアファイル編集」「LinuxとWindows両対応」「.cmd更新」「.ps1更新」などのキーワードでも起動する。
---

# Linux/Windowsペアファイル編集支援

## 適用条件

`~/dotfiles/` でLinux/Windowsのペアファイルのいずれかを編集するときに適用する。
片方のみ変更すると配布経路の一方が不整合になる。

## ペアファイルの判別

ペアファイルはfrontmatterに示すファイル名規則（`.sh`と`.cmd`、`.sh`と`.ps1`、
`.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`）で判別する。
編集対象がいずれかに該当する場合は、対応するもう一方を作業対象に含める。

作業着手時に両側の対応関係を確認する
（例: `install.sh`のオプション追加後、`install.ps1`への同一オプション追加が漏れる）。

## 新規ペアの追加

ペア命名規則に従ってファイル名を決め、両OS分を同時に追加する。
新規ペアの種類によっては`.chezmoiignore`への除外エントリ追加も必要になる（chezmoiがOSごとに適切なファイルをデプロイするため）。

## PowerShell / `.ps1.tmpl` 側の必須作法

改行・厳格モード・エンコーディング指定・パス操作などの記述作法は
`<plugin root>/skills/coding-standards/references/powershell.md`に従う。
ペアファイル側で追加する事項は次の2点とする。

- BOMなしUTF-8で出力する場合は`System.Text.UTF8Encoding`のインスタンスを使う
  （既存`install-claude.ps1`の`$script:utf8NoBom`を参照する）
- `$HOME`と`$env:USERPROFILE`のどちらを使うかをスクリプト内で統一し、両者を併用しない

## Bash / `.sh.tmpl` 側の対応

記述作法は`<plugin root>/skills/coding-standards/references/bash.md`に従う。
ただし`.sh.tmpl`では既存スクリプトに合わせて`set -eux`を使う。
Windows版と同じ処理を別の記法で書いているだけの場合、両方に同一のコメントを付けて対応関係を示す。

## 変更フロー

1. 編集対象がfrontmatterのファイル名規則に該当するか確認し、対応するもう一方のパスを特定する
2. 意味的な変更を両方に適用する
3. プラットフォーム固有の書き方の違いのみ確認する
4. 可能であれば両方を実行して動作確認する（Linuxでのみ実行可能な環境では最低限syntax check）
5. MCP経由の`run_for_agent`へ両プラットフォーム側のファイルパスを渡す。
   複数ツールを組み合わせる場合は`commands`で対象を限定する。
   MCPを利用できない場合は`uvx pyfltr run-for-agent`を使う
6. コミットメッセージにペアを両方記載する
