# Git識別子の扱い

`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」が委ねる、報告と操作で用いるGit識別子を定める。

Git操作、内部比較、報告、記録、成果物では、人間が対象を判別できる識別子（branch名、tag名、PR番号、run URL、計画名、WIファイル名、レーン識別子など）を優先する。
それが無いcommitは`git rev-parse --short=7 <revision>`が返す7文字以上の一意な短縮OIDで表す（`--short`だけの最小長は`core.abbrev`に従う）。観測結果は`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月14日」にある。
`git rev-parse --short=7`へは1回に1件のrevisionだけを渡す。複数渡すと`fatal: Needed a single revision`で失敗する。
peel式は実行shellで単一引数にする。PowerShellでは`git rev-parse --verify 'HEAD^{commit}'`のように単一引用符で囲む。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月20日」にある。

識別子は実行結果として得た値をそのまま使い、記憶や推測で組み立てない。誤った識別子は別対象への操作を招き、受け取ったエージェントが実在しない対象を待つ。

外部コマンドやAPIが40桁か64桁のOIDを要求する場合は、その操作の直前に同じ実行ホストで`$(git rev-parse <revision>)`のように解決して変数へ代入し、終了コード、空でないこと、形式を確かめてからその操作だけに使う。会話や過去の出力から書き写した値は使わない。置換の失敗が空文字列へ展開すると`git push origin :<ref>`のような別の意味の引数が成立する。
完全OIDは工程間へ伝播させず、工程間には短縮OIDを渡す。`SKILL.md`「WI実装commitの対応」の各コマンドは対象worktreeで完全OIDへ解決してから比較する。

現在の作業ディレクトリと別のリポジトリを操作するGitコマンドは`git -C <解決済み絶対パス>`で対象を各コマンドに明示し、`cd`の結果を後続コマンドへ引き継がない。
