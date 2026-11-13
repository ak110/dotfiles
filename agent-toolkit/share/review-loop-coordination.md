# 実行レビュー反復の調整

実行レビューの委譲元は、指摘の受領、修正指示、再レビュー、およびエスカレーションの各工程を収束まで統括して調整する。
レビューイーは、`agent-toolkit:process-wi`では対象レーンのレーン担当、`agent-toolkit:write-plan`をメインエージェントが起動した場合はメイン自身、公開工程のCI失敗修正では公開担当が起動する`CI修正担当`とする。
通常の受領、修正、再レビューの進行では「ラウンドの遷移」から「応答済みの判定」までの規定を適用する。
再レビューで指摘を受け取った際は、具体的に判断する前に`review-loop-coordination-recurrence.md`「反復原因の判定」を読み込む。
判断要請を受け取った際は、具体的に判断する前に`review-loop-coordination-escalation.md`の「エスカレーションの事由別の扱い」と「技術判断で外せる完成条件」を読み込む。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 再レビューの指摘を受領し修正指示を確定する前 | `review-loop-coordination-recurrence.md` |
| 判断要請を受領し対処を確定する前 | `review-loop-coordination-escalation.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ラウンドの遷移・ラウンド番号の保持先・応答済みの判定に関する判断・作業の前 | `review-loop-coordination-rounds.md` |
| 反復原因の判定に関する判断・作業の前 | `review-loop-coordination-recurrence.md` |
| エスカレーションの事由別の扱い・技術判断で外せる完成条件に関する判断・作業の前 | `review-loop-coordination-escalation.md` |
