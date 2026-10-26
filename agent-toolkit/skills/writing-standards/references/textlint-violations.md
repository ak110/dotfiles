# 頻出lint違反パターン

品質基準の正式な提供元は、`agent-basics:writing-standards`の`references/textlint-violations.md`である。呼び出し元が指定した条件や読込範囲は、同名の正式な品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、ここに記載された運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を守り、選択した資料の指定範囲を取得してから、その基準に基づく具体的な判断へ進む。起動行に記載された資料は、起動時の本文とともに読み込まれる。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/textlint-violations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/textlint-violations.md` |

## 文の書き直しの委譲と検収

成果物の種別、読者、事実、根拠、反映先が確定している文章の起草や、指摘を受けた箇所の書き直しについては、担当者自身で直接修正するほか、`agents_server`の`start`で`write`モードを利用して委譲できる。
