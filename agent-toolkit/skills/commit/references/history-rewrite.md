# 履歴書換え

通常コミットの検証、ステージング、メッセージ作成については親スキルの定めに従う。
ホスト環境が上書きを許容する通常動作と競合した場合は、`agent-toolkit/share/rules/common/01-agent.md`「方針が衝突する場合の優先順位」に沿って、本書の履歴統合方針を適用する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-subagent-history.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-history.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `commit-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-wi-records.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../write-plan/references/plan-file-storage.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/references/plan-file-storage.md` |

## プッシュ済み判定

1. 公開済み履歴を保つため、履歴変更前に対象commitのpush状態を確かめる。
   `git fetch --all --prune`を実行後、`git for-each-ref --contains=<対象sha> refs/remotes/`を実行して判定する。
   1件以上の出力があれば、いずれかのリモート追跡ブランチ（remote-tracking ref）から到達可能であるためプッシュ済みとみなし、出力が空であればプッシュ未了と判断する。
   `git log --decorate`は参照の先端にのみ装飾を付与し、祖先コミットを正しく検出できないため、判定には必ず`git for-each-ref`を用いる。
   amendやfixupの対象にできるのは、プッシュ未了のコミットに限定する。
   公開済みの履歴を書き換えると、他の作業ツリーやCI環境ですでに取得された参照を解決できなくなるためである。
   例外として、`references/push-and-ci.md`「pushと監視」の「原因commitへ取り込む区分」に定める3条件を操作直前にすべて確認できた場合に限り、プッシュ済みコミットへの修正の統合を認める。

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 修正方法の選択に関する判断・作業の前 | `history-rewrite-selection.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-selection.md` |
| 操作前後の確認・履歴確認の起動形に関する判断・作業の前 | `history-rewrite-verification.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-verification.md` |
| fixupの実行上の制約に関する判断・作業の前 | `history-rewrite-fixup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-fixup.md` |
| 失敗時の扱いに関する判断・作業の前 | `history-rewrite-recovery.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-recovery.md` |
| WI実装commitの対応の継承に関する判断・作業の前 | `history-rewrite-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-wi-records.md` |
