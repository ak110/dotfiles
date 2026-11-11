# 検証結果の診断と警告の判定

本書は、検証を担当したエージェントが、診断メッセージや警告の内容を基準バージョンとの差分に基づいて判定し、その結果や不足事項を依頼元へ報告するための判断基準を定める。
報告先は、検証を依頼した主体とする。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 欠陥を示す診断の処置を確定する前 | `agent-basics:bugfix/SKILL.md` |
| 所要の増大や不要仕事を原因調査へ渡す前 | `agent-basics:bugfix/SKILL.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 読む出力・合否の判定に関する判断・作業の前 | `diagnostics-results.md` |
| 基準版と変更行への帰属に関する判断・作業の前 | `diagnostics-baseline.md` |
