# 実装レビュー担当の起動とレビュー修正

`plan-executor`が実装単位のHEADを実装レビュー担当へレビューさせ、指摘へのレビュー修正担当の起動と履歴統合を管理する際に本書へ従う。
実装レビュー担当自身の手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`を正本とする。
レビュー修正担当自身の手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`を正本とする。

## `review_contract`の生成

`plan-executor`は全実装単位の完了と最終検証を受領した後、初回の実装レビュー担当を起動する直前に`review_contract`を生成する。
計画ファイル（メイン）の目的、実施内容に記録された採否・除外・保持、エージェント判断及び変更履歴と、計画ファイル（詳細）の実装資料、開始時点の完全OIDで特定した実体並びに起動元が保持する計画外の明示入力を生成元とする。

`review_contract`は、ユーザー目的、現行の公開契約、ユーザー合意、入力生成主体、信頼境界、通常入力、対象外入力、誤許可と誤拒否の消費主体への影響を、独立して成否を判定できる条項ごとに分ける。
各条項は次の形式で起動プロンプトへ直接記載する。

```text
review_contract:
  - 条項: <対象と判定内容>
    内容: <契約本文、又は参照先>
    出典: <計画ファイルの絶対パスと見出し・表の行、開始時点の完全OIDとリポジトリ相対パス及び識別子、又は明示入力の出所>
```

計画ファイルの記述だけで条項の内容が一意に定まる場合は、`内容`を`参照: <計画ファイルの絶対パス>#<見出し又は表の行>`として渡せる。
参照先を追加で解釈しなければ判定内容が定まらない場合は、契約本文を`内容`へ記載する。
複数の条項を1件へ集約せず、前段が列挙する各観点と出典を欠かさない。
`review_contract`はいずれの永続ファイルにも追記せず、初回起動後は同じ実装レビュー担当のthreadへ保持する。

## レビュー修正

通常の実装レビューは対象計画へ照合した後、同じコンテキストで`review_contract`へ照合する。公開契約基準には最初の実装担当の起動前に検収したworktreeの完全OIDを使う。新規に実装レビュー担当を起動する場合は`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`の絶対パスと生成済みの`review_contract`を入力へ加えて渡す。
レビュー担当へ渡す開始時点の完全OIDは、渡す直前に`git -C <対象worktreeの絶対パス> rev-parse --verify --quiet <検収したOID>^{commit}`を実行し、終了コード0で出力された文字列をそのまま渡す。
終了コードが0でない場合はレビュー担当を起動せず、実行したコマンドと終了コードを呼び出し元へ返す。記憶、転記又は短縮形からの復元でOIDを組み立てない。

同worktreeだけへ単一の修正用の実装担当を割り当て、新規起動では`agents_server.start`へ`model_type="execute"`を渡す。継続接続では同じ`model_type`を保持する。初回実装担当routeと今回routeの遷移は`agent-toolkit:delegation`の経路選択契約に従う。

新規起動では`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`と`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`を渡す。
あわせて元の実装入力、`agent-toolkit:reviewee-standards`のSKILL.md、フィードバックファイル名一覧、複製元と対象外worktreeも渡す。
起動文へ担当種別を`レビュー修正担当`として明示する。
計画ファイルと同じディレクトリの`<計画stem>.exec-review.tsv`を用いる実装レビュー指摘管理表の絶対パスと
`implementation-review`の`track`は、fast担当又はfix担当の初回起動入力に含める。
修正担当はレビュー表とworktreeの実体から指摘の採否、対象の実装単位commit及び修正方針を確定する。
調整担当はレビュー表を読まず、採否、対応付け又は成果物・Git・検証結果を再検収しない。

レビュー修正の実装、レビュー表への履歴書換え証拠の保存、履歴欠落時の回復及び履歴統合は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`、履歴書換えの遮断は`agent-toolkit:commit`の履歴書換え契約を正本とする。修正担当が`対応完了`を返した場合は同じレビューthreadへ`再レビューせよ`を送り、`対応完了（再レビュー不要）`の場合はレビューを省略する。
