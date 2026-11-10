# 遮断・警告フックの成立条件と設計の型

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` |
| 案内済み状態と取得済み状態の仕様を使う前 | `session-state-and-flags-common.md`、`session-state-and-flags-document-receipts.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags-common.md`、`read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags-document-receipts.md` |

## 遮断と警告の選択

`formatter`および`warning_formatter`は、同一の会話・呼び出し主体・操作範囲において、原因・対象・対処が共通する通知を初回に限定して届ける。
原因が解消した後の再発や、本文と次の操作に変更が生じた場合は、新規の通知として扱う。
反復回数に応じた遮断への昇格は行わない。
既存の呼び出し元が渡す`removable_cause`、`escalate_on_repeat`、`summary`は、入力の受理形式を維持するために受け付けるが、回数通知や昇格の判定には使用しない。
実害を防止するためのblock（遮断）は操作条件に基づいて判定し、同一の操作であっても必要な遮断を毎回維持する。

読み込み支援は必要な初回案内に限定し、未取得を理由に再書き込みを遮断しない。
`pretooluse/read_delivery.py`は、主体・対象の必要範囲・本文版ごとに実配送した案内と取得証拠を区別する。
未取得の継続だけでは案内を反復せず、実質的な対象・対応の変更や文脈喪失後の現在の必要資料を改めて案内する。
状態は`session-state-and-flags-document-receipts.md`「確認・操作スキル系」にある。
