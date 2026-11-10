# 実行レビューの指摘と完成条件証拠の記録

本書では、実行レビュー担当が指摘をレビュー指摘管理表へ登録し、完成条件の証拠を記録する手順と、判定値および根拠の記述方法を定める。
判定基準は`exec-review-criteria-execution.md`「実行レビューの判定」に従い、返却形式は`exec-review.parent.md`「実行レビューの返却契約」に従う。

完成条件証拠とは、実行レビュー担当が返却メッセージに記述する、各条件の判定結果をまとめた行の集まりである。
判定とその根拠は、実行レビュー担当自身だけが記述する。
レビューイーに追加の確認や証拠の補完を求めた場合でも、補完された情報をもとに判定し記入するのは実行レビュー担当である。
実装担当やメインエージェントが提示する`検証結果`（`verification-evidence-records.md`「完成条件証拠の検証記録」）は判定材料に過ぎず、最終的な判定は実行レビュー担当自身が下す。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 未コミット検証の結果を適用判定する前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` |
| 返却形式を確認する時 | `exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-criteria-execution.md` | 判断 | `read:agent-toolkit:*/share/exec-review-criteria-execution.md` |
| 差分外の候補を実在する不良として分類する前 | `agent-basics:writing-standards/references/investigation-sources.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-sources.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `verification-evidence-records.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-records.md` |
| 保存済みの成功集合と限定再実行から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/wi-standards-origin.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-standards-origin.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination-escalation.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination-escalation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.parent.md`、`exec-parent-integration.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md`、`read:agent-toolkit:*/share/exec-parent-integration.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration-subagent-inputs.md` | 判断 | `read:agent-toolkit:*/share/lane-integration-subagent-inputs.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-parent-correction.md` | 判断 | `read:agent-toolkit:*/share/exec-review-parent-correction.md` |
| 検証の実行範囲を判断する前 | `agent-toolkit:/share/exec-review-operations.md` | 判断 | `read:agent-toolkit:*/share/exec-review-operations.md` |

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/exec-review.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.subagent.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レビュー指摘管理表への登録・走査記録と差分外の不良に関する判断・作業の前 | `exec-review-recording-issues.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-issues.md` |
| 完成条件証拠の作成・条件別判定行の形式・受理済み判定への参照に関する判断・作業の前 | `exec-review-recording-evidence.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-evidence.md` |
| review_contractの書式・記録する行と要求単位に関する判断・作業の前 | `exec-review-recording-contract.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-contract.md` |
| 判定の値・失効の根拠・公開後に成立する条件と任意の判断材料に関する判断・作業の前 | `exec-review-recording-verdicts.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-verdicts.md` |
| 統合時の完成条件判定に関する判断・作業の前 | `exec-review-recording-integration.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-integration.md` |
| 根拠の書き方に関する判断・作業の前 | `exec-review-recording-grounds.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-grounds.md` |
