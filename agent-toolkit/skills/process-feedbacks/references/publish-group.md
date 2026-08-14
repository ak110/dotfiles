# 公開グループ

順序条件とPR/MR操作を併せ持つグループ終端項目は、依存先の採用済み実装変更型項目を1つのソースブランチへ統合して公開する。

- 順序状態の正本は`depends_on`だけとし、公開構成集合とは分離する。
- 公開構成集合には採用済み実装変更型の依存先だけを登録する。
- 同じ構成項目を要求するグループでは、filenameが最小のグループ終端項目を所有者とする。
- 公開構成集合が空の場合は終端工程を実行せず、観測を根拠にTBDへ送る。
- callerは最初の構成項目の計画を受領した時点で`atk managed-temp create --prefix publish-group`を実行し、領域とmarkerを計画の進捗ログへ記録する。
- callerは新規領域の作成前に`atk managed-temp list --prefix publish-group`でmarkerを列挙する。同じ`group_final_item`と`target_repo`のmarkerが0件なら新規作成し、1件なら内容を一意に照合して既存領域を再利用し、複数件又は照合不一致なら公開操作を停止してTBDへ送る。
- 領域とmarkerの保存後、進捗ログ記録前に中断した場合も同じget-or-create判定から再開する。再利用した領域の公開グループ表と外部状態を照合して未実施工程だけを続行し、二重の領域作成又は公開操作を行わない。
- 公開グループmarkerは固定名`.publish-group-marker.json`とし、管理markerとは別ファイルにする。
- markerは`schema_version`（数値、初版は1）、`group_final_item`（文字列）、`target_repo`（正規化リモートURLの文字列）、`created_at`（UTC ISO 8601の文字列）の4必須フィールドだけを持つJSON objectとする。
- markerは排他的作成で書き込み、内容のflush後にfsyncする。既存markerがある場合は失敗として扱う。
- markerの読込前にsymlinkではない通常ファイルであること、現在利用者が所有しprivate registryと同じprivate権限であることを確認する。JSON解析後は4必須フィールドの欠落、余分な項目、型又は値の不正を照合失敗として扱う。
- managed-tempのmarkerとprivate registryの照合は、
  `agent-toolkit:delegation`の`references/claude-code-runtime.md`を正本とする。
- 領域内の公開グループ表は、終端項目、構成項目、ブランチ、OID、PR/MR識別子及び完了済み操作の永続SSOTとする。
- 中断後は`depends_on`、計画、進捗ログ、領域パスの順で再発見し、`atk managed-temp list --prefix publish-group`とmarkerで照合する。
- 構成項目は対象ブランチへ直接pushせず、ソースブランチのpush済みOIDをCI通過後にadoptする。
- グループ終端項目はPR/MRの作成、CI通過、本文明記によるマージ、対象ブランチのCI通過を確認してマージ後OIDでadoptする。
- 失敗時は既存の保留契約に従い、領域はグループ終端項目のadopt完了後だけ回収する。
