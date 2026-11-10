# CI失敗の分類と適切な対処

CIの失敗について、原因箇所、今回の変更（セッション）への帰属、再現性の3つの観点から客観的な観測事実に基づいてそれぞれ分類し、原因そのものを根本から解消する対処法を選ぶ。
原因が自明で単純な失敗であれば複数の対立仮説を細かく評価する手順は省略できるが、拡張原因分析が必要かどうかの判断は`root-cause-analysis-initial.md`「初動と拡張原因分析の判定」に従って行う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 拡張原因分析の要否を判断する前 | `root-cause-analysis-initial.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-initial.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 失敗の性質による分類・対処法の選択肢と判定基準に関する判断・作業の前 | `ci-failure-handling-classification.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling-classification.md` |
| 分類判断のための詳細検討に関する判断・作業の前 | `ci-failure-handling-investigation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling-investigation.md` |
