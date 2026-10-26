# 設計時のコーディング品質

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/design-time.md`である。呼び出し元が指定した条件と読込範囲は、同名の品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しを参照し、指定がない場合は全文を読む契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、本書に残る運用節を参照する。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料の指定範囲を取得した上で、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-time.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/message.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/message.md` |

## 破壊的変更の記録

設定キーや環境変数の改名、統合、削除に伴い、品質資料の移行条件に照らして破壊的変更として記録する場合は、commitメッセージにおいて`agent-toolkit:commit`の`references/message.md`で定義された`!`を付与する。
