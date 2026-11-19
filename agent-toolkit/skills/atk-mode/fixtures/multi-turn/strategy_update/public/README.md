# 明細の公開契約

公開API`total_cents(records)`は明細配列の合計を整数セントで返す。
各明細は`amount`と`unit`を持つ。
`unit`が`cents`なら`amount`は整数セント、`dollars`なら小数文字列のドルであり、1ドルは100セントである。
ドル金額は小数点以下2桁までとする。
既存の整数セント明細の結果を保つ。

`feed_a.json`と`feed_b.json`が2系統の入力例である。
先行担当AとBの記録も同梱する。
