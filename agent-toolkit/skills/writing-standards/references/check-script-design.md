# 機械チェックスクリプトの設計原則

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/check-script-design.md`である。呼び出し元が指定した条件と読込範囲は、同名の品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しを参照し、指定がない場合は全文を読む契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、本書に残る運用節を参照する。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料の指定範囲を取得した上で、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/check-script-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/check-script-design.md` |

## 構造化文書の解析

設計の詳細は`agent-toolkit/agent_toolkit/_plan/structure/__init__.py`のモジュールdocstringを参照する。
