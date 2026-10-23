# 遮断・警告フックの成立条件と設計の型

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-block-warn.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` |

## 遮断と警告の選択

`formatter`と`warning_formatter`は、同じ会話・呼出主体・操作範囲で原因・対象・対処が同じ通知を初回だけ届ける。
原因の解消後の再発と、本文や次の操作の変化は新しい通知とする。反復回数による遮断への昇格は行わない。
既存呼出元の`removable_cause`・`escalate_on_repeat`・`summary`は受理形式を保つため受け付け、回数通知と昇格には使わない。
実害を防ぐblockは操作の条件から判定し、同じ操作でも必要な遮断を毎回保持する。
