---
name: sync-platform-pair
description: >
  Linux/Windowsペアファイル（`.sh`と`.cmd`、`.sh`と`.ps1`、
  `.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`など）を編集するときに使う。
---

# Linux/Windowsペアファイル編集支援

## 適用条件

本リポジトリでLinux/Windowsのペアファイルのいずれかを編集するときに適用する。該当の判定は後掲のファイル名規則で行う。
両側をそろえて変更する。片方だけの変更は配布先の一方を不整合にする。

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
`<plugin root>/skills/writing-standards/references/powershell.md`に従う。
ペアファイル側で追加する事項を次に挙げる。

- BOMなしUTF-8で出力する場合は`System.Text.UTF8Encoding`のインスタンスを使う
  （既存`install-claude.ps1`の`$script:utf8NoBom`を参照する）

## Bash / `.sh.tmpl` 側の対応

記述作法は`<plugin root>/skills/writing-standards/references/bash.md`に従う。
ただし`.sh.tmpl`の`set`のオプションは既存の`.chezmoi-source/run_after_post-apply.sh.tmpl`に合わせる。

## ローカルで実行するlintとCIジョブの対応

ローカルの`make test`で実行されないCIジョブは`dotfiles-development`「開発手順」の全体検証の項が挙げる。

Linux側とWindows側で分岐するコードを変更した場合、Windows側の分岐は`make test`では検証されず、CIの`test-windows`ジョブが検証する。
`agent-toolkit:writing-standards`の`references/testing.md`「プラットフォーム分岐の検証」に従い、OS判定に使う値を引数で受け取るヘルパーへ集約し、分岐値をパラメーター化テストで両方通す。

## 変更フロー

1. 「ペアファイルの判別」に従い、対応するもう一方のパスを特定する
2. 意味的な変更を両方に適用する
3. プラットフォーム固有の書き方の違いのみ確認する
4. 実行できる側を実行して動作確認する（Linuxでのみ実行可能な環境では、Windows側は最低限syntax check）
5. `dotfiles-development`「開発手順」の特定ファイルに限定する実行形へ、両プラットフォーム側のファイルパスを渡す
6. コミットメッセージにペアを両方記載する
