# 頻出lint違反パターン

品質基準の正式な提供元は、`agent-basics:writing-standards/references/textlint-violations.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/textlint-violations.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/textlint-violations.md` |

## 頻出違反パターンの予防策

agent-toolkitの計画形式では、計画メタ情報の項目名（`起動経路`など）や固定の節名は、計画構造の定数と一致する名前で示す。日本語と半角文字が隣接する節名をかぎ括弧で囲むと、JTF 3.1.1の自動修正で隣接スペースが削除され、計画構造の自動チェックで節名が一致せず誤検出される。

## 文の書き直しの委譲と検収

成果物の種別、読者、事実、根拠、反映先が確定している文章の起草や、指摘を受けた箇所の書き直しについては、担当者自身で直接修正するほか、`agents_server`の`start`で`write`モードを利用して委譲できる。
