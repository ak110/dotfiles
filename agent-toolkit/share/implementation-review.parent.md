# 実装レビュー担当の起動とレビュー修正

`plan-executor`が実装単位のHEADを実装レビュー担当へレビューさせ、指摘へのレビュー修正担当の起動と履歴統合を管理する際に本書へ従う。
実装レビュー担当自身の手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`を正本とする。
レビュー修正担当自身の手順は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`を正本とする。

## `review_contract`の生成

`plan-executor`は全実装単位の完了と最終検証を受領した後、初回の実装レビュー担当を起動する直前に`review_contract`を生成する。
計画ファイル（メイン）の目的、実施内容に記録された採否・除外・保持、エージェント提案詳細及び変更履歴と、計画ファイル（詳細）の実装資料、開始時点の完全OIDで特定した実体並びに起動元が保持する計画外の明示入力を生成元とする。

実装レビュー担当を起動する起動文と、再レビューを指示する文には、`round: <ラウンド番号>`の行を含める。ラウンド番号は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## ラウンド番号の正本`が定める値とする。

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
当該threadが継続不能と確定した場合は、初回起動時と同じ生成元から`review_contract`を再生成して新しい実装レビュー担当へ渡す。
あわせて、`レビュー種別: 引き継ぎ再レビュー`、レビュー指摘管理表の絶対パス、`implementation-review`の`track`、レビュー対象HEADの完全OID及び直前修正の直接影響範囲を渡す。
さらに、`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`の`## 入力`が要求する必須入力のうち、前段と重複しない次の項目を全て渡す。

- 計画ファイル（メイン・詳細）、対象リポジトリ及び対象worktreeの絶対パス
- 作業種別が`バグ対応`の計画では計画ファイル（バグ）の絶対パス
- プロジェクト規範、適用する作成規範スキル及び`agent-toolkit:review-standards`のSKILL.mdの絶対パス
- 計画の実装単位、目的、変更説明、開始時点の完全OID、変更ファイル一覧、検証結果及び進捗の現在状態

任意入力の`対象ファイル限定`を用いる場合は、当該対象も渡す。
計画、対象リポジトリ、対象worktree、規範、実装単位及び開始時点の完全OIDは`plan-executor`の保持情報から、レビュー対象HEAD、変更ファイル一覧、検証結果及び進捗はレビュー指摘管理表と対象worktreeの現行実体から解決する。
継続不能の判定と引き継ぎ手順は`agent-toolkit:delegation`の経路選択契約に従う。

実装レビュー担当を起動する前に、`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`の`## 入力`が列挙する必須入力が起動文にそろっていることを確認する。生成した`review_contract`が前段の列挙する各観点と出典を欠かさないことと、絶対パスで示す入力が実在することも同じ時点で確認する。いずれかが欠ける場合は実装レビュー担当を起動せず、欠けていた入力項目又は条項を呼び出し元へ返す。

## レビュー修正

通常の実装レビューは対象計画へ照合した後、同じコンテキストで`review_contract`へ照合する。公開契約基準には最初の実装担当の起動前に検収したworktreeの完全OIDを使う。初回の実装レビュー担当を新規に起動する場合は、`レビュー種別: 初回レビュー`、`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`の絶対パス及び生成済みの`review_contract`を入力へ加えて渡す。
レビュー担当へ渡す開始時点の完全OIDは、渡す直前に`git -C <対象worktreeの絶対パス> rev-parse --verify --quiet <検収したOID>^{commit}`を実行し、終了コード0で出力された文字列をそのまま渡す。
終了コードが0でない場合はレビュー担当を起動せず、実行したコマンドと終了コードを呼び出し元へ返す。記憶、転記又は短縮形からの復元でOIDを組み立てない。

同worktreeだけへ単一の修正用の実装担当を割り当て、新規起動では`agents_server.start`へ`model_type="execute"`を渡す。継続接続では同じ`model_type`を保持する。初回実装担当routeと今回routeの遷移は`agent-toolkit:delegation`の経路選択契約に従う。

新規起動では`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`と`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.subagent.md`を渡す。
あわせて元の実装入力、`agent-toolkit:reviewee-standards`のSKILL.md、フィードバックファイル名一覧、複製元と対象外worktreeも渡す。
起動文へ担当種別を`レビュー修正担当`として明示する。
実装レビュー指摘管理表の絶対パスと`implementation-review`の`track`は、fast担当又はfix担当の初回起動入力に含める。
ファイル名と配置は`agent-toolkit:plan-mode`の計画ファイル基準が定める。
修正担当はレビュー表とworktreeの実体から指摘の採否、対象の実装単位commit及び修正方針を確定する。
調整担当はレビュー表を読まず、採否、対応付け又は成果物・Git・検証結果を再検収しない。

レビュー修正の実装、レビュー表への履歴書換え証拠の保存、履歴欠落時の回復及び履歴統合は`${CLAUDE_PLUGIN_ROOT}/share/implementation.subagent.md`、履歴書換えの遮断は`agent-toolkit:commit`の履歴書換え契約を正本とする。修正担当が`対応完了`を返した場合は同じレビューthreadへ`再レビューせよ`と`round: <ラウンド番号>`の2行を送り、`対応完了（再レビュー不要）`の場合はレビューを省略する。
