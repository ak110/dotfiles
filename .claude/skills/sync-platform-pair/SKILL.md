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

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../develop-dotfiles/references/verification-values-execution.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-execution.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../develop-dotfiles/references/verification-values-publishing.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-publishing.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| PowerShell（`.ps1`・`.ps1.tmpl`）側を編集する前 | `agent-basics:writing-standards/references/powershell.md` | 操作 | `path:*.ps1`、`path:*.ps1.tmpl` |
| Bash（`.sh`・`.sh.tmpl`）側を編集する前 | `agent-basics:writing-standards/references/bash.md` | 操作 | `path:*.sh`、`path:*.sh.tmpl` |
| Linux側とWindows側で分岐するコードを変更する前 | `../../../agent-basics/skills/writing-standards/references/testing-platforms.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-platforms.md` |

## ペアファイルの判別

ペアとなるファイルは、frontmatter記載のファイル名規則に基づいて判別する。
これには`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、`.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`などが含まれる。
作業に着手する際は、もう一方のファイルも必ず作業対象に含める。

## 編集の要点

- 実質的な処理内容の変更は双方のファイルへ反映し、両者の差異は各プラットフォーム固有の記法や作法の違いのみにとどめる。
- 実行可能なプラットフォーム側のファイルは実際に動かして動作を確認し、直接実行できない側のファイルについても最低限の構文チェック（構文エラーがないことの確認）を行う。
  検証する際は、`develop-dotfiles`の`../develop-dotfiles/references/verification-values-execution.md`「検証コマンドの実行」に記載された、対象ファイルを限定して実行するコマンド形式に両ファイルのパスを渡して実行する。
- 新しくペアファイルを作成する場合は、両方のOS向けファイルを同時に追加する。
  ファイルの種類によっては、OSごとのデプロイを正しく制御するために`.chezmoiignore`への除外設定の追加も必要となる。
- PowerShell側でBOMなしUTF-8のテキストを出力する場合は、`System.Text.UTF8Encoding`のインスタンスを使用する（`install-claude.ps1`における`$script:utf8NoBom`の実装を参照）。
- `.sh.tmpl`に記述する`set`コマンドのオプションは、`.chezmoi-source/run_after_post-apply.sh.tmpl`の記述スタイルに合わせる。

## ローカルで実行するチェックとCIジョブの対応関係

Windows実OS固有の動作は、CI上の`test-windows`などによる観測と、Linuxの`make test`での分岐値の模擬を区別する。
`agent-basics:writing-standards/references/testing-platforms.md`「プラットフォーム分岐の検証」に従い、判定値をパラメーター化して両方の分岐を検証し、fixtureが共有モジュールと実ホストAPIへ及ぼす影響も確認する。
引数・終了状態・パス表現・権限・ファイル共有の実OSでの成功は、模擬した分岐選択の成功と分けて検証記録へ残す。
配布時の初期状態や起動時の依存解決はローカルでも前提を比較し、実Windowsでしか確かめられない条件を未確認のまま保証へ含めない。
候補branchへのpushによるWindows検証は、必要性・認可・重複費用を確認して選べる。
ローカルの`make test`では実行されないCIジョブの一覧については、`develop-dotfiles`の`../develop-dotfiles/references/verification-values-publishing.md`「push前のチェックとCIだけが実行するチェック」を参照する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| dotfilesの開発手順の基準を適用する前 | `../develop-dotfiles/SKILL.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/SKILL.md` |
