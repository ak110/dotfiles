---
name: sync-platform-pair
user-invocable: false
description: >
  LinuxとWindowsのペアファイル（`.sh`と`.cmd`、拡張子なしの`bin/<name>`と`bin/<name>.cmd`、`.sh`と`.ps1`、
  `.sh.tmpl`と`-windows.ps1.tmpl`、`*.posix.json`と`*.win32.json`など）を編集するときに使う。
---

# Linux/Windowsペアファイルの編集支援

LinuxとWindowsのペアファイルの片方を編集する時に、両者の動作と意味をそろえる手順である。
ペアはfrontmatterのファイル名規則で判別し、着手の時点でもう一方のファイルも作業対象に含める。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| PowerShell（`.ps1`・`.ps1.tmpl`）側を編集する前 | `agent-basics:writing-standards/references/powershell.md` |
| Bash（`.sh`・`.sh.tmpl`）側を編集する前 | `agent-basics:writing-standards/references/bash.md` |

## 編集の要点

1. 実質的な処理の変更は両方のファイルへ入れ、差はプラットフォーム固有の記法と作法だけにとどめる。
   LinuxとWindowsで分岐するコードの試験の書き方は`agent-basics:writing-standards/references/testing.md`に従う。
2. 新しいペアを作る時は両方のOS向けのファイルを同時に足す。
   ファイルの種類によっては、OSごとの配布を制御するため`.chezmoiignore`へ除外の設定を足す。
3. PowerShell側でBOMなしUTF-8のテキストを出す時は`System.Text.UTF8Encoding`のインスタンスを使う（`install-claude.ps1`の`$script:utf8NoBom`）。
4. `.sh.tmpl`の`set`のオプションは`.chezmoi-source/run_after_post-apply.sh.tmpl`の書き方に合わせる。
5. 実行できる側のファイルは実際に動かし、直接実行できない側も構文を確かめる。
   両方のパスを`.claude/skills/develop-dotfiles/references/verification.md`「検証コマンドの実行」の対象を限定した起動の形に渡す。
6. Windowsの実OS固有の動作と、ローカルの`make test`が実行しないCIジョブは、`.claude/skills/develop-dotfiles/references/verification.md`「push前のチェックとCIだけが実行するチェック」で確かめる。
