# Git識別子の扱い

`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」が委ねる、報告と操作で用いるGit識別子を定める。

Git操作、内部比較、報告、記録、成果物では、人間が対象を判別できる識別子（日付、件名、branch名、tag名、PR・MR番号、run URL、計画名、WIファイル名、レーン識別子など）を優先する。
人が読む記述での選び方は`agent-toolkit:writing-standards`の`references/writing.md`「文章の共通規定」に従う。エージェントがcommitを再取得する記述では`git rev-parse --short=7 <revision>`が返す7文字以上の一意な短縮OIDで表す（`--short`だけの最小長は`core.abbrev`に従う）。観測結果は`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月14日」にある。
`git rev-parse --short=7`へは1回に1件のrevisionだけを渡す。複数渡すと`fatal: Needed a single revision`で失敗する。
peel式は実行shellで単一引数にする。PowerShellでは`git rev-parse --verify 'HEAD^{commit}'`のように単一引用符で囲む。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月20日」にある。

識別子は実行結果として得た値をそのまま使い、記憶や推測で組み立てた値は使用対象から除く。誤った識別子は別対象への操作を招き、受け取ったエージェントが実在しない対象を待つ。

外部コマンドやAPIが40桁か64桁のOIDを要求する場合は、その操作の直前に同じ実行ホストで`$(git rev-parse <revision>)`のように解決して変数へ代入し、終了コード、空でないこと、形式を確かめてからその操作だけに使う。会話や過去の出力から書き写した値は使用対象から除く。置換の失敗が空文字列へ展開すると操作の意味が変わることがある。pushのrefspecでは空の送り元がremote refの削除を意味するため、解決した値を確かめてから渡す。
完全OIDは工程間へ伝播させず、工程間には短縮OIDを渡す。`SKILL.md`「WI実装commitの対応」の各コマンドは対象worktreeで完全OIDへ解決してから比較する。

現在の作業ディレクトリと別のリポジトリを操作するGitコマンドは`git -C <解決済み絶対パス>`で対象を各コマンドに明示し、`cd`の結果を後続コマンドへ引き継がない。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/writing.md#「文章の共通規定」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/writing.md#「文章の共通規定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「WI実装commitの対応」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「WI実装commitの対応」` |
