# plan-review-executorの起動と受領

`agent-toolkit:plan-and-add-feedback`と`agent-toolkit:plan-mode`の直接起動経路で、起草済み計画をレビューさせる工程の開始時に本書を全文読む。
起動主体は本書を`plan-review-executor`の起動、checkpointの受領及び`計画レビュー完了`の検収へ適用する。
本書は`plan-review-executor`自身が行う手順を定義しない。

## 起動

起動主体は`plan-review`の`track`用のレビュー指摘管理表を新設し、新規の`plan-review-executor`へ次の入力を渡す。

- 計画ファイル（メイン）・計画ファイル（詳細）の絶対パス
- 対象リポジトリ
- プロジェクト規範
- レビュー指摘管理表の絶対パス

## checkpointの受領

`review_round`は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って受領する。

`計画レビュー完了`を受領したら、計画ファイルの内容と収束状況を検収し、書込所有権を引き継いで次工程へ進む。
`status: needs_escalation`を受領した場合は、事象、根拠及び必要な判断をユーザーへ確認し、回答だけを同じ`plan-review-executor`へ返す。
