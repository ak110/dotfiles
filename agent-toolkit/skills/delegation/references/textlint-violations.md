# 頻出lint違反パターン

品質基準の正式な提供元は、`agent-basics:writing-standards/references/textlint-violations.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 頻出違反パターンの予防策

agent-toolkitの計画形式では、計画メタ情報の項目名（`起動経路`など）や固定の節名は、計画構造の定数と一致する名前で示す。
日本語と半角文字が隣接する節名をかぎ括弧で囲むと、JTF 3.1.1の自動修正で隣接スペースが削除され、計画構造の自動チェックで節名が一致せず誤検出される。

## 文の書き直しの委譲と検収

成果物の種別、読者、事実、根拠、反映先が確定している文章の起草や、指摘を受けた箇所の書き直しについては、担当者自身で直接修正するほか、`agents_server`の`start`で`write`モードを利用して委譲できる。
