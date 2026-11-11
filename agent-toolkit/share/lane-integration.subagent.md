---
mode: delegate
model_type: execute
required_inputs:
- 統合区分
- 実行レビュー済みHEAD
- 統合先worktree
- 統合先branch
- 計画ファイル名一覧
- AWI終了区分
- レーン開始時刻
- 書込担当一覧
optional_inputs:
- 完成条件証拠
- プロジェクト固有の公開後の操作の順序
writes_worktree: true
---

# レーン統合タスク

<!-- parent-read-source: agent-toolkit:/share/exec.parent.md#統合の指示と受領 -->

計画の起草と実装を担当したレーン担当スレッドが、専用ブランチの統合と作成した計画の最終化を実施し、AWIの採否を確定する。
リモートへのpushは委譲元が担当する。
専用worktreeとブランチは統合後も追加作業に備えて保持し、その回収は、全工程および外部プロセスの完了後に所有登録に基づいて資源を回収する通常の手順や、process-loopの子プロセスの終了後処理で行う。

担当工程に着手する前に、`share/work-contract.md`「工程境界での要求と成果の確認」に従って`全体作業契約`を読み、判定結果を自身の記録に残す。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` |
| manifestの`verification`へ渡す検証の範囲を決める前 | `agent-basics:check-execution/references/verification-scope-selection.md` |
| 省略された変更の保持とWI継承を検収する時 | `../skills/commit/references/history-rewrite-wi-records.md` |

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 「マージありの統合」でrebaseが競合で停止したとき、またはrebase後の変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-integration-failure.md](../skills/process-wi/references/lane-integration-failure.md) |
| `マージあり`の統合を開始する時（統合時の完成条件判定を行う時） | `exec-review-recording-integration.md` |
| 「マージありの統合」の検証を実行した後 | `agent-basics:check-execution`の`references/diagnostics.md` |
| `adopt`・`reject`のメモを書く直前 | `../skills/wi-standards/SKILL.md`、`../skills/wi-standards/references/states-and-dependencies-completion.md` |

## マージなしの統合

**7.** 実装がなければ統合先を維持して最終化する。
手順5または7の完了後、「出力」の形式で返す。

公開統合操作のコマンドに`--no-merge`を追加し、`--reviewed-head`には今回の専用worktreeの現在OIDを渡す。
統合先ブランチは統合開始時点の状態を維持し、その短縮OIDを`統合後のHEAD`とする。
未終了項目の計画を作業領域に残す指示がある場合も、全計画を保存したうえで、セッション終了時にメインエージェントが復元する運用とする。

## 出力

`変更したエージェント向け文書`には、統合先worktreeで`atk run-script agent-doc-changes -- <統合前HEAD> <統合後のHEAD>`を単独実行し、終了コード0で取得したJSON配列をそのまま使用する。
統合前HEADは、このレーンの短い更新の直前の統合先HEAD（所有登録の`.integration.new_base`、rebaseしなかった場合は`.integration.verified_base`）とする。
`マージなし`の場合は空配列とする。
同一レーンの統合を再度実行する場合は、前回までに返却した値との重複のない和集合を返す（メインエージェントはこの値を用いてレーン全体の規範変更を再取得する）。
返却するたびに、その値を引き継ぎ記録先へ記録する。

次の固定形式のみで結果を返す。

```text
統合完了
統合後のHEAD: <7文字以上の一意な短縮OID>
保存した計画ファイル: <["計画ファイル名",...]形式のJSON配列。計画なしは[]。入力と同じ順序で重複なし>
adoptしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejectしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
adoptを延期したAWIとcommit: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OIDまたは実装差分が無い場合の空文字列"}]形式のJSON配列。無い場合は[]>
変更したエージェント向け文書: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

続行不能と追加報告行は、共通契約`rules/rules-subagent.md`「返却形式の受け渡し」に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 入力に関する判断・作業の前 | `lane-integration-subagent-inputs.md` |
| 計画最終化・AWI行の終了に関する判断・作業の前 | `lane-integration-subagent-finalization.md` |
| 公開統合操作に関する判断・作業の前 | `lane-integration-subagent-operation.md` |
