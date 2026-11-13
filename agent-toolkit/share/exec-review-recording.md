# 実行レビューの指摘と完成条件証拠の記録

本書では、実行レビュー担当が指摘をレビュー指摘管理表へ登録し、完成条件の証拠を記録する手順と、判定値および根拠の記述方法を定める。
判定基準は`exec-review-criteria-execution.md`「実行レビューの判定」に従い、返却形式は`exec-review.parent.md`「実行レビューの返却契約」に従う。

完成条件証拠とは、実行レビュー担当が返却メッセージに記述する、各条件の判定結果をまとめた行の集まりである。
判定とその根拠は、実行レビュー担当自身だけが記述する。
レビューイーに追加の確認や証拠の補完を求めた場合でも、補完された情報をもとに判定し記入するのは実行レビュー担当である。
実装担当やメインエージェントが提示する`検証結果`（`verification-evidence-records.md`「完成条件証拠の検証記録」）は判定材料に過ぎず、最終的な判定は実行レビュー担当自身が下す。

内部群のレビューでは、固定した保存HEAD、割当要求、直接影響集合と成功証拠を基準に、その群が被覆した元WIの条件と未達・割当外の条件を区別して記録する。
群の局所的な成功を元WI全体の`達成`へ読み替えず、割当外の条件は`exec-review-recording-contract.md`「記録する行と要求単位」に従って除外理由の一致を判定する。
その行の`達成`は割当の一致だけを示し、割当先の成果の未確認・未達を根拠に残して、最終レビューで元の完成条件そのものを判定する。
最終レビューでは群別の返却・管理表・契約と元WIの全条件を対応付け、同じ本文・環境・契約の受理済み判定と検証を参照して再利用する。
追加の判定は群間の契約、共有生成物、組合せ差分と残る未達条件へ集約し、群別の被覆の和集合だけで全体の達成とは判定しない。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 未コミット検証の結果を適用判定する前 | `verification-evidence-observation.md` |
| 返却形式を確認する時（操作対象外: 自身の返却形式の確認） | `exec-review.parent.md` |
| 保存済みの成功集合と限定再実行から被覆を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` |
| 検証の実行範囲を判断する前 | `agent-toolkit:/share/exec-review-operations.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| レビュー指摘管理表への登録・走査記録と差分外の不良に関する判断・作業の前 | `exec-review-recording-issues.md` |
| 完成条件証拠の作成・条件別判定行の形式・受理済み判定への参照に関する判断・作業の前 | `exec-review-recording-evidence.md` |
| review_contractの書式・記録する行と要求単位に関する判断・作業の前 | `exec-review-recording-contract.md` |
| 判定の値・失効の根拠・公開後に成立する条件と任意の判断材料に関する判断・作業の前 | `exec-review-recording-verdicts.md` |
| 統合時の完成条件判定に関する判断・作業の前 | `exec-review-recording-integration.md` |
| 根拠の書き方に関する判断・作業の前 | `exec-review-recording-grounds.md` |
