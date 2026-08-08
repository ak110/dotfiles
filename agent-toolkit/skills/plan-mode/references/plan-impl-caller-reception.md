# 計画実装担当の起動と受領

呼び出し元が`plan-impl-executor`を起動し、完了実体を検収してpushとCIを引き取る。

## 起動

対象worktreeが上流追随済みでcleanであることと、同じworktreeにwriterがいないことを確認する。
起動文は受信者への命令を先頭に置き、次だけを渡す。

- 計画ファイル、対象worktree、プロジェクト規範の絶対パス
- feedback filename
- 追加指示と許容済みの挙動変化。該当しない場合は`なし`
- 複製元と対象外worktree、commit可、push不可などの権限

agent定義とtask referenceが持つ手順、schema、完了条件を起動文へ複製しない。

## 受領

executorの要約を受領したら、本文より先に次を実測する。

1. 計画が明示した単位に対応するcommitと変更ファイル。単位の指定が無い場合は計画全体に対応する単一commit
2. cleanな作業ツリーと担当外差分の有無
3. 近接検証と最終検証の実行結果
4. 二系統reviewの対象commit、読み取り専用状態、指摘と対応結果
5. 計画の完了条件と`## 進捗ログ`

callerは各commit単位の受領時と最終レビュー時に`## 進捗ログ`を更新する。
記録対象は現在状態、commit、検証、計画との差異、blockerとし、固定形式や全操作履歴を要求しない。
実装中に判断を変えた場合は`## 変更履歴`へ起点、採否、現在の結論、同期先を追記し、
人間向け固定領域の本文と矛盾しない状態に保つ。

報告本文と実体が異なる場合は実体を優先する。一意に補正できる値は呼び出し元が補正し、
実作業不足、証拠不足、候補が複数残る場合だけ未完了事項へ縮減してexecutorへ返す。
開始SHA、全roundの応答全文、大規模な固定report schemaは要求しない。

## pushとCI

実装委譲はcommitと二系統reviewまでとする。呼び出し元が`agent-toolkit:commit`の
`references/push-and-ci.md`を読み、pushとCI通過確認を所有する。
CI失敗時は`agent-toolkit:bugfix`で原因を確定し、必要な修正、検証、commit、二系統reviewを再実施する。
CI通過後にfeedbackの後始末へ進む。
