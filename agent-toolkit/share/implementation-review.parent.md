# 実装レビュー担当の起動とレビュー修正

`plan-impl-executor`が実装単位のHEADを実装レビュー担当へレビューさせ、指摘へのレビュー修正担当の起動と履歴統合を管理する際に本書へ従う。
実装レビュー担当自身の手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`を正本とする。
レビュー修正担当自身の手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`を正本とする。

## レビュー修正

通常の実装レビューは対象計画へ照合した後、同じコンテキストで`review_contract`へ照合する。公開契約基準には最初の実装担当の起動前に検収したworktreeの完全OIDを使う。新規に実装レビュー担当を起動する場合は`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`の絶対パスを入力へ加えて渡す。

同worktreeだけへ単一の修正用の実装担当を割り当て、修正用の実装担当を新規起動する直前に`atk config get execute_fix_model`を実行する。継続接続の直前も同じ設定値を再取得する。初回実装担当routeと今回routeの遷移は`agent-toolkit:delegation`の経路選択契約に従う。

新規起動では`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`と`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`を渡す。
あわせて元の実装入力、`agent-toolkit:reviewee-standards`のSKILL.md、フィードバックファイル名一覧、複製元と対象外worktreeも渡す。
起動文へ担当種別を`レビュー修正担当`として明示する。
レビュー表の絶対パスと`implementation-review`の`track`は、fast担当又はfix担当の初回起動入力に含める。
修正担当はレビュー表とworktreeの実体から指摘の採否、対象の実装単位commit及び修正方針を確定する。
調整担当はレビュー表を読まず、採否、対応付け又は成果物・Git・検証結果を再検収しない。

レビュー修正の実装、レビュー表への履歴書換え証拠の保存、履歴欠落時の回復及び履歴統合は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`、履歴書換えの遮断は`agent-toolkit:commit`の履歴書換え契約を正本とする。修正担当が`対応完了`を返した場合は同じレビューthreadへ`再レビューせよ`を送り、`対応完了（再レビュー不要）`の場合はレビューを省略する。
