---
name: sync-platform-pair
user-invocable: false
description: >
  Linux/Windowsペアファイル（`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、
  `.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`など）を編集するときに使う。
---

# Linux/Windowsペアファイル編集支援

本スキルはLinux/Windowsのペアファイルを両側そろえて編集する手順を提供する。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| PowerShell（`.ps1`・`.ps1.tmpl`）側を編集する前 | `<plugin root>/skills/writing-standards/references/powershell.md` |
| Bash（`.sh`・`.sh.tmpl`）側を編集する前 | `<plugin root>/skills/writing-standards/references/bash.md` |
| Linux側とWindows側で分岐するコードを変更する前 | `<plugin root>/skills/writing-standards/references/testing.md`の「プラットフォーム分岐の検証」 |

## 適用条件

本リポジトリのペアの片方を編集するときに適用し、両OSの意味をそろえる。対象は次節の規則で判定する。

## ペアファイルの判別

ペアファイルはfrontmatterに示すファイル名規則（`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、
`.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`）で判別する。
着手時に両側の対応を確認し、もう一方も作業対象へ含める。

## 変更フロー

1. 「ペアファイルの判別」に従い、対応するもう一方のパスを特定する
2. 意味的な変更を両方に適用する
3. プラットフォーム固有の書き方の違いのみ確認する
4. 実行できる側を実行して動作確認する（Linuxでのみ実行可能な環境では、Windows側は最低限syntax check）
5. `dotfiles-development`「開発手順」の特定ファイルに限定する実行形へ、両プラットフォーム側のファイルパスを渡す
6. コミットメッセージにペアを両方記載する（努力目標。履歴から両側の変更をたどりやすくする）

## 新規ペアの追加

前節「ペアファイルの判別」のファイル名規則に従ってファイル名を決め、両OS分を同時に追加する。
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

CIが実行しないチェックを確定する一般の手順は`agent-toolkit:commit`の`references/publish.md`「検証とCI」が定める。
ローカルの`make test`で実行されないCIジョブは`dotfiles-development`の`references/verification-values.md`「push前のチェックとCIだけが実行するチェック」が挙げる。

Linux側とWindows側で分岐するコードを変更した場合、Windows側の分岐は`make test`では検証されず、CIの`test-windows`ジョブが検証する。
`agent-toolkit:writing-standards`の`references/testing.md`「プラットフォーム分岐の検証」に従い、OS判定に使う値を引数で受け取るヘルパーへ集約し、分岐値をパラメーター化テストで両方通す。
