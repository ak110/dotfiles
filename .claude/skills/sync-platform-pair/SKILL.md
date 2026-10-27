---
name: sync-platform-pair
user-invocable: false
description: >
  LinuxとWindowsのペアファイル（`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、
  `.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`など）を編集するときに使う。
---

# Linux/Windowsペアファイルの編集支援

このスキルは、LinuxとWindows向けに用意されたペアファイルの片方を編集する際に、両者の動作や意味の整合性を保つための規約をまとめたものである。

## 名指し参照の読込方法

本文中の各参照について適用条件を確認し、選んだ資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の表にある資料は、起動本文とともに提供される。

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

ペアとなるファイルは、frontmatter記載のファイル名規則に基づいて判別する。これには`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、`.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`などが含まれる。作業に着手する際は、もう一方のファイルも必ず作業対象に含める。

## 編集の要点

- 実質的な処理内容の変更は双方のファイルへ反映し、両者の差異は各プラットフォーム固有の記法や作法の違いのみにとどめる。
- 実行可能なプラットフォーム側のファイルは実際に動かして動作を確認し、直接実行できない側のファイルについても最低限の構文チェック（構文エラーがないことの確認）を行う。検証する際は、`dotfiles-development`「開発手順」に記載された、対象ファイルを限定して実行するコマンド形式に両ファイルのパスを渡して実行する。
- 新しくペアファイルを作成する場合は、両方のOS向けファイルを同時に追加する。ファイルの種類によっては、OSごとのデプロイを正しく制御するために`.chezmoiignore`への除外設定の追加も必要となる。
- PowerShell側でBOMなしUTF-8のテキストを出力する場合は、`System.Text.UTF8Encoding`のインスタンスを使用する（`install-claude.ps1`における`$script:utf8NoBom`の実装を参照）。
- `.sh.tmpl`に記述する`set`コマンドのオプションは、`.chezmoi-source/run_after_post-apply.sh.tmpl`の記述スタイルに合わせる。

## ローカルで実行するチェックとCIジョブの対応関係

Linux側とWindows側で処理を分岐させているコードのうち、Windows側の分岐処理はローカル環境の`make test`では検証されず、CI上の`test-windows`ジョブによって検証される。そのため、`agent-basics:writing-standards`の`references/testing.md`「プラットフォーム分岐の検証」に従い、OSの判定に利用する値を引数として受け取るヘルパー関数等に処理を集約し、パラメーター化テストを用いて両方の分岐をテストで網羅できるように実装する。
ローカルの`make test`では実行されないCIジョブの一覧については、`dotfiles-development`の`references/verification-values.md`「push前のチェックとCIだけが実行するチェック」を参照する。
