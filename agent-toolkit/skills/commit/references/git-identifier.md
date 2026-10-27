# Git識別子の扱い

本書は、`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」に基づき、作業の報告やコマンド操作で使用するGit識別子の扱い方を定める。

Gitの操作、内部比較、作業報告、記録、および成果物の作成にあたっては、人間が対象を判別しやすい識別子（日付、コミット件名、ブランチ名、タグ名、PR・MR番号、実行ログのURL、計画名、WIファイル名、レーン識別子など）を優先して使用する。
人間向けの記述における識別子の選び方は、`agent-toolkit:writing-standards`の`references/writing.md`「文章の共通規定」に従う。一方、エージェントがコミットを機械的に再取得する記述では、`git rev-parse --short=7 <revision>`が返す7文字以上の一意な短縮OIDを用いる（`--short`のみを指定した場合の最小長はGit設定の`core.abbrev`に依存する）。この挙動の検証結果は、`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月14日」に記録されている。
なお、`git rev-parse --short=7`には1回につき1件のリビジョンのみを渡す。複数のリビジョンを同時に渡すと`fatal: Needed a single revision`エラーで失敗する。
また、リビジョンのpeel式をシェルで評価する際は、全体を単一の引数として渡す。たとえばPowerShellでは、`git rev-parse --verify 'HEAD^{commit}'`のようにシングルクォートで囲む。この監査記録は、`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/git-identifier.md：Git識別子の扱い：2026年9月20日」にある。

識別子には、コマンドの実行結果として実際に得られた値をそのまま利用する。記憶や推測によって組み立てた値は除外する。誤った識別子を指定すると、意図しない対象に対する操作につながったり、後続のエージェントが存在しない対象を待ち続けたりする原因となる。

外部コマンドやAPIが40桁または64桁の完全なOIDを要求する場合は、操作する直前に同じ実行環境で`$(git rev-parse <revision>)`のように解決して変数に代入する。そのうえで、コマンドの終了コードが正常であること、値が空でないこと、および形式が正しいことを確認してから、その操作にのみ使用する。過去の会話履歴や古い出力から手作業で書き写した値は除外する。コマンド置換に失敗して空文字列が渡されると、操作の意味が意図せず変化することがある。とくに`git push`のrefspecでは、送信元が空になるとリモート参照（remote ref）の削除を意味してしまうため、解決した値を確実に検証してから渡す。
完全OIDの工程間への引き継ぎは避ける。工程間の受け渡しには短縮OIDを用いる。`SKILL.md`「WI実装commitの対応」に記載された各コマンドは、対象のworktree内で完全OIDに解決したうえで比較する。

現在の作業ディレクトリとは異なるリポジトリを操作するGitコマンドは、`git -C <解決済み絶対パス>`を用いて対象ディレクトリを各コマンドへ明示的に渡す。`cd`の移動結果を後続のコマンドに引き継ぐ運用は避ける。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/writing.md#「文章の共通規定」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/writing.md#「文章の共通規定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「WI実装commitの対応」` | 判断 | `section:agent-toolkit:*/skills/commit/SKILL.md#「WI実装commitの対応」` |
