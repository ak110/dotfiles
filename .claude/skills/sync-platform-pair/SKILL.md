---
name: sync-platform-pair
user-invocable: false
description: >
  Linux/Windowsペアファイル（`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、
  `.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`など）を編集するときに使う。
---

# Linux/Windowsペアファイル編集支援

本スキルはLinux/Windowsのペアファイルの片方を編集するときに、両側の意味をそろえるための規約を提供する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-development/SKILL.md#「開発手順」` | 判断 | `section:*/skills/dotfiles-development/SKILL.md#「開発手順」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-development/references/verification-values.md#「push前のチェックとCIだけが実行するチェック」` | 判断 | `section:*/skills/dotfiles-development/references/verification-values.md#「push前のチェックとCIだけが実行するチェック」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| PowerShell（`.ps1`・`.ps1.tmpl`）側を編集する前 | `agent-basics:writing-standards/references/powershell.md` | 操作 | `path:*.ps1`、`path:*.ps1.tmpl` | |
| Bash（`.sh`・`.sh.tmpl`）側を編集する前 | `agent-basics:writing-standards/references/bash.md` | 操作 | `path:*.sh`、`path:*.sh.tmpl` | |
| Linux側とWindows側で分岐するコードを変更する前 | `agent-basics:writing-standards/references/testing.md`の「プラットフォーム分岐の検証」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/testing.md#「プラットフォーム分岐の検証」` | |

## ペアファイルの判別

ペアはfrontmatterのファイル名規則（`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、`.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`）で判別する。着手時にもう一方も作業対象へ含める。

## 編集の要点

- 意味的な変更は両側へ適用し、違いはプラットフォーム固有の書き方だけにとどめる
- 実行できる側は実行して確かめ、実行できない側も最低限syntax checkする。検証は`dotfiles-development`「開発手順」の特定ファイルに限定する実行形へ両側のパスを渡す
- 新規ペアは両OS分を同時に追加する。種類によってはOSごとのデプロイのため`.chezmoiignore`への除外エントリも要る
- PowerShell側でBOMなしUTF-8を出力する場合は`System.Text.UTF8Encoding`のインスタンスを使う（`install-claude.ps1`の`$script:utf8NoBom`）
- `.sh.tmpl`の`set`のオプションは`.chezmoi-source/run_after_post-apply.sh.tmpl`に合わせる

## ローカルで実行するlintとCIジョブの対応

Linux側とWindows側で分岐するコードのWindows側は`make test`では検証されず、CIの`test-windows`ジョブが検証する。`agent-basics:writing-standards`の`references/testing.md`「プラットフォーム分岐の検証」に従い、OS判定に使う値を引数で受け取るヘルパーへ集約し、分岐値をパラメーター化テストで両方通す。
ローカルの`make test`で実行されないCIジョブは`dotfiles-development`の`references/verification-values.md`「push前のチェックとCIだけが実行するチェック」が挙げる。
