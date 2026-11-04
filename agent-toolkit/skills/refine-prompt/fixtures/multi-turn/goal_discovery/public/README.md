# 注文集計の公開契約

`python task.py report <JSONファイル>`は注文のJSON配列を読み、`count`と`total_cents`を持つJSONオブジェクトを標準出力へ返す。
各注文は整数セントの`amount_cents`を持つ。
空配列は件数と合計金額をともに0とする。
`orders.json`は正常なサンプルである。

既存の`legacy_total(values)`は数値配列の合計を返す公開APIである。
