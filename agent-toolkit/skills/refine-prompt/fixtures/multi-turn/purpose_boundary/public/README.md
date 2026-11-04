# 改善要求の提出契約

公開API`proposal()`は辞書を返す。
`purpose`には目指す状態を日本語の文字列で、`changes`には改善する領域の識別子文字列の配列を指定する。
資料の領域と識別子は、性能`performance`、作業発見`discovery`、観測による更新`strategy`、複数担当の統合`coordination`、検証`verification`である。
目的と範囲については依頼者へ質問できる。

task.pyのproposal()として保存しローカルで実行確認する。
外部への投稿・commit・pushは不要。
