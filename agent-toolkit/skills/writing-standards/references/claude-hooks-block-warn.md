# 遮断・警告フックの成立条件と設計の型

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`である。呼び出し元が指定した条件と読込範囲は、同名の品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しを参照し、指定がない場合は全文を読む契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、本書に残る運用節を参照する。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料の指定範囲を取得した上で、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` |

## 遮断と警告の選択

`formatter`および`warning_formatter`は、同一の会話・呼び出し主体・操作範囲において、原因・対象・対処が共通する通知を初回に限定して届ける。
原因が解消した後の再発や、本文と次の操作に変更が生じた場合は、新規の通知として扱う。反復回数に応じた遮断への昇格は行わない。
既存の呼び出し元が渡す`removable_cause`、`escalate_on_repeat`、`summary`は、入力の受理形式を維持するために受け付けるが、回数通知や昇格の判定には使用しない。
実害を防止するためのblock（遮断）は操作条件に基づいて判定し、同一の操作であっても必要な遮断を毎回維持する。
