# 実行と状態変更の設計

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 状態や業務ルールに関わる排他・トランザクションの単位を導く前 | `design-time-state.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time-state.md` |
| 処理自体の失敗を異常終了で表すかを判断する前 | `../../bugfix/references/root-cause-prevention-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-selection.md` |
| 本文の参照基準を適用する前 | `implementation-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time.md` |
| 本文の参照基準を適用する前 | `testing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing.md` |
| 本書の基準を適用する前 | `design-heuristics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 並行処理・排他制御の設計観点に関する判断・作業の前 | `design-runtime-concurrency.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-concurrency.md` |
| キャッシュ設計に関する判断・作業の前 | `design-runtime-cache.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-cache.md` |
| 非同期ジョブ・リトライ設計に関する判断・作業の前 | `design-runtime-jobs.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-jobs.md` |
| 常駐サービス設計・負荷制御と外部呼び出しの設計に関する判断・作業の前 | `design-runtime-services.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-services.md` |
| 技術仕様と実利用環境の評価に関する判断・作業の前 | `design-runtime-environment.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-environment.md` |
| 状態を変える多段手順の工程順に関する判断・作業の前 | `design-runtime-procedure-order.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-procedure-order.md` |
