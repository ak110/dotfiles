---
mode: delegate
model_type: execute
result_heads:
- 統合完了
required_inputs:
- 統合区分
- 実行レビュー済みHEAD
- 計画ファイル名一覧
- AWI終了区分
- 書込担当一覧
optional_inputs:
- 完成条件証拠
- プロジェクト固有の公開後の操作の順序
writes_worktree: true
---

# レーン統合タスク

レーンの採否によらず統合と終端の操作を同じ担当がそろえて行い、委譲元がpushと公開へ取り違えなく進めるようにする。
計画の起草と実装を担当したレーン担当のthreadが、専用branchの統合と作成した計画の最終化を行い、AWIの採否を確定する。
リモートへのpushは委譲元が担う。
入力の意味、`atk lane integrate`の引数とmanifest、計画の最終化、AWI行の終了は`agent-toolkit:atk-mode/references/lane-integration-format.md`にあり、統合の前に全文を読む。

## 入力

必須の入力は`統合区分`、`実行レビュー済みHEAD`、`計画ファイル名一覧`、`AWI終了区分`、`書込担当一覧`であり、任意の入力は`完成条件証拠`と`プロジェクト固有の公開後の操作の順序`である。
`所有登録`、`統合先worktree`、`統合先branch`、`レーン開始時刻`、`引き継ぎ記録先`は、レーン担当の起動時に受け取った値を使う（`agent-toolkit:atk-mode/roles/exec.subagent.md`「入力」）。
各入力の意味と確かめ方は`agent-toolkit:atk-mode/references/lane-integration-format.md`「入力」にある。

## 処理

複数レーンの集約は`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「統合キュー」に従い、代表の一括取り込みと各レーンの最終化に共通の検証証拠を使う。

1. `統合区分`が`マージあり`なら`agent-toolkit:atk-mode/references/lane-integration-format.md`「マージありの統合」、`マージなし`なら同書「マージなしの統合」で統合する。
   `マージあり`の統合を始める時は`agent-toolkit:atk-mode/references/review-record-acceptance.md`で統合時の完成条件を判定する。
   manifestの`verification`へ渡す検証の範囲は`agent-basics:check-execution/references/verification-scope.md`で決め、検証の実行後は`agent-basics:check-execution/references/diagnostics.md`で診断を読む。
   プロジェクトが統合後のCIへ全量テストを任せる場合は、レーンの変更と競合解消に関わる検証を最終の内容で確かめ、全量のCI結果とは分けて記録する。
2. mergeが競合で止まった時と、merge後の変更範囲の検証が失敗した時は、`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「mergeの競合」「統合後の変更範囲の検証の失敗」へ進む。
3. `agent-toolkit:atk-mode/references/lane-integration-format.md`「計画最終化」「AWI行の終了」の操作をmanifestへ渡し、`phase: finalized`を確かめてから「出力」の形式で返す。
   `adopt`と`reject`のメモを書く直前に`agent-toolkit:atk-mode/playbooks/wi-drafting.md`と`agent-toolkit:atk-mode/references/states-and-dependencies.md`を読む。

## 出力

`変更したエージェント向け文書`には、統合先worktreeで`atk run-script agent-doc-changes -- <統合前HEAD> <統合後のHEAD>`を単独実行し、終了コード0で取得したJSON配列をそのまま使用する。
統合前HEADは、このレーンの短い更新の直前の統合先HEAD（所有登録の`.integration.new_base`、mergeしなかった場合は`.integration.verified_base`）とする。
`マージなし`の場合は空配列とする。
同一レーンの統合を再度実行する場合は、前回までに返却した値との重複のない和集合を返す（メインエージェントはこの値を用いてレーン全体の規範変更を再取得する）。
返却するたびに、その値を引き継ぎ記録先へ記録する。

次の固定形式のみで結果を返す。

```text
統合完了
統合後のHEAD: <7文字以上の一意な短縮OID>
保存した計画ファイル: <["計画ファイル名",...]形式のJSON配列。公開の時点で計画なしで進行中のレーンは[]。入力と同じ順序で重複なし>
adoptしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejectしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
adoptを延期したAWIとcommit: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OIDまたは実装差分が無い場合の空文字列"}]形式のJSON配列。無い場合は[]>
変更したエージェント向け文書: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
