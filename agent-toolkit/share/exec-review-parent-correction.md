# 実行レビュー担当の起動とレビュー修正：レビュー修正

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 差分外の不良を受領して対処時機を決める前 | `../skills/delegation/references/receiving-followup.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving-followup.md` | |
| 形式判別前 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` | |
| 基準の参照時 | `exec-subagent-implementation.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-implementation.md` | |
| 検証結果を記録する前 | `verification-evidence-records.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-records.md` | |
| 手動観測を保存する前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` | |
| 最終の検証と有限の手動観測の時機を判断する前 | `../skills/commit/references/commit-verification.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-verification.md` | |
| 保存済み成功と失敗限定の結果を合成する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` | |
| 基準の参照時 | `usability-review.parent.md` | 判断 | `read:agent-toolkit:*/share/usability-review.parent.md` | |
| 初回起動前 | `../skills/delegation/references/process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` | |
| process-wi中に差分外の既存不良や正式改善AWIを受領した時 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` | |
| 基準の参照時 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` | |
| 基準の参照時 | `review-loop-coordination-rounds.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination-rounds.md` | |
| 基準の参照時 | `../skills/delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` | |
| 基準の参照時 | `exec.parent.md`、`exec-parent-integration.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md`、`read:agent-toolkit:*/share/exec-parent-integration.md` | |
| 基準の参照時 | `exec-review-recording-integration.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-integration.md` | |
| 返却の`達成`行の根拠を受領確認する前 | `exec-review-recording-grounds.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-grounds.md` | |
| 基準の参照時 | `../skills/delegation/references/sending.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/sending.md` | |
| 基準の参照時 | `review-loop-coordination.md`、`review-loop-coordination-rounds.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination.md`、`read:agent-toolkit:*/share/review-loop-coordination-rounds.md` | |
| 基準の参照時 | `exec-parent-launch.md` | 判断 | `read:agent-toolkit:*/share/exec-parent-launch.md` | |
| 基準の参照時 | `exec-review-responses-completion.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses-completion.md` | |
| 本文が再レビュー省略の条件を確認する時 | `agent-basics:review-standards/references/reviewee-verification.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee-verification.md` | |
| 基準の参照時 | `lane-integration-subagent-operation.md` | 判断 | `read:agent-toolkit:*/share/lane-integration-subagent-operation.md` | |
| 基準の参照時 | `agent-toolkit:write-plan/references/plan-file-storage.md` | 判断 | `read:agent-toolkit:*/skills/write-plan/references/plan-file-storage.md` | |
| 完成条件証拠の検収時に、計画や委譲プロンプトから複数のWI名を取得する場合 | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-show-bulk.md` | |
| `review_contract`の要否を判断し、生成・再生成する前 | `exec-review-contract-generation.md` | 判断 | `read:agent-toolkit:*/share/exec-review-contract-generation.md` | |
| レビュー工程の判定・記録を始める前 | `review-table-operations.md` | 判断 | `read:agent-toolkit:*/share/review-table-operations.md` | |
| レビュー工程の判定・記録を始める前 | `exec-review-criteria.md` | 判断 | `read:agent-toolkit:*/share/exec-review-criteria.md` | |
| レビュー工程の判定・記録を始める前 | `exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` | |
| レビュー工程の判定・記録を始める前 | `exec-review-recording.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording.md` | |

## レビュー修正

**8.** 指摘への応答と修正結果を受領し、再判定が要る場合は手順6へ戻る。

修正の統括は毎ラウンド全指摘を読み、元要求・計画・契約・既存証拠・応答と比較する。
前提・必要性・修正規模への疑問は理由と指摘を対応付け、同じ依頼の`レビュー参考意見`へ添える。
追加のコード探索・再現・詳細調査はせず、不明点は疑問として渡す。
指摘原文・参考意見・採否を区別し、疑いだけで指摘を消去・対応済みにしない。
process-wiとwrite-planの双方に適用する。

同じレビューsessionへの再依頼は`send_message`の`instruction: 再レビューせよ`と`extra_params`で行い、役割の継続と後述の最新情報を渡す。
組み込みの委譲機構を使う場合は、`agent-toolkit:delegation`の`references/sending.md`の生成用CLIを使う。

修正はworktree所有者が担い、体制は`review-loop-coordination.md`に従う。
同じthreadへ`send_message`の`instruction: レビュー指摘の対応をせよ`を送り、`extra_params`へ全計画の`レビュー指摘管理表`（1件は絶対パス、複数は絶対パスのJSON配列）と、あれば`レビュー参考意見`を渡す。
表と計画の対応は`exec-review-parent-launch.md`「起動方法別のレビュー基準」に従い、roundは表から解決する。
自由文も同じ名前付き入力を使う。
継続不能なら`../skills/delegation/SKILL.md`「継続と新規起動」と`exec-parent-launch.md`「入力」に従い、`レビュー修正担当`を起動する。
`再開位置`へ`~/.claude/plans`の計画絶対パス、管理表と参考意見は前段と同じ形式で渡し、引き継ぎ記録の全計画との対応を保持する。
採否、対象commit、修正方針、成果物・Git状態・検証の検収は修正担当が所有し、`exec-subagent-implementation.md`、`exec-subagent-history.md`に従う。
統括の内容確認は参考意見の作成までとする。

修正担当の`対応完了`・`対応完了（再レビュー不要）`に`検証結果`と`画面差分`が揃わなければ再提出させる。
全指摘を不採用として根拠と応答を記録し、採用0件となった`対応完了`も通常どおり再レビューへ進める。
修正commit・履歴統合の有無で返却種別を判定せず、未応答0件を確かめる。
`対応完了`では`review-loop-coordination-rounds.md`「応答済みの判定」で未応答0件を確認し、同じレビューthreadへ`再レビューせよ`と`round: <ラウンド番号>`を送る。
送信直前に開始時からの累積差分で別計画の認可commitを確認し、存在すれば`exec-review-contract-generation.md`「`review_contract`の生成」に従って判定対象外commitを最新OIDへ更新する。
初回条項も含む契約を`review_contract: <絶対パス>`で渡す。
前回後の履歴書換えには`../skills/commit/references/history-rewrite-wi-records.md`「WI実装commitの対応の継承」が保存したJSONを`rewrite_map: <絶対パス>`（引き継ぎ再レビューでは`extra_params`）で渡す。
`画面差分: あり`なら同時に`usability-review.parent.md`に従う再判定（未起動は初回）も依頼する。
`対応完了（再レビュー不要）`では`agent-basics:review-standards/references/reviewee-verification.md`「方式見直しと再レビュー」の機械的修正、詳細・実装レベルだけの修正、低リスク段階の追加条件への適合を確かめる。
低リスク段階だけは修正後HEADで`exec-review-responses-completion.md`「完了報告」の判定コマンドを実行する。
会話が途切れた再開、修正に関わる未達・根拠不足の再判定、修正との因果関係が不明な場合は通常の`対応完了`と同じく再レビューする。
直前の返却の条件別判定行に、`exec-review-recording-integration.md`「統合時の完成条件判定」で受理できない、修正に関わる`未達`や`根拠不足`の行が残る場合も再レビューする。
判定結果は、再判定した実行レビュー担当が更新する。

再判定が不要と確かめた場合は実行レビューを省き、次の手順で統合入力HEADを確定する。
amendやautosquashでHEADのコミットOIDが変わるため、統合入力HEADと、判定行を出した時点の`レビューHEAD`は区別して保持する。

1. `画面差分: あり`で新しい画面資産が加わっている場合は、ユーザビリティレビューを省かず同じHEADに対して依頼する
2. 「応答済みの判定」で、未応答の指摘が0件であることと、指摘管理表の該当行に修正前後の短縮OIDの対応が記録されていることを確かめる
3. 専用branchのHEADを`git rev-parse --short=7`で解決し、指摘管理表にある修正後の短縮OIDと一致することを確かめる
4. その値を統合入力HEAD（`exec.parent.md`、`exec-parent-integration.md`参照）として保持する。
   `lane-integration-subagent-operation.md`「マージありの統合」のコミット一致確認はこの値で行う
5. 受領確認では、最後に実際にレビューした時点の`レビューHEAD`と突き合わせ、判定行の対象コミットを修正後のHEADへ形式的に付け替えない

## レビュー指摘管理表の保存

**9.** 収束後の検収・再開に必要な管理表を、基準に応じて保存する。

- 基準が`WI`のみの場合: レーン統合では`atk lane integrate`が`phase: finalized`を返した後に、直接実装では本書の受領確認と意味検収を終えた後に、指摘管理表を削除する。
  統合の検査と再開で表を参照するため、最終化が終わるまでは保持する。
  保存先はprivate-notesの管理外とする
- 基準が`CI記録`のみの場合: 同じ修正系列では同じ指摘管理表と`修正系列の開始時のHEAD`を引き継ぎ、系列の完了時に`atk plans commit ci-<修正系列の開始時のHEAD>.exec-review.tsv`で`private-notes/plans/ci/`へ保存する。
  保存の時機は`agent-toolkit:commit/references/ci-failure-handling.md`の「入力計画」に従い、`process-wi`の公開工程では公開担当が実行する
- 基準に`計画`を含む場合: 計画を管理するエージェントが、`agent-toolkit:write-plan`の`references/plan-file-storage.md`「計画ファイルの保存と参照」に従って計画バンドルとともに保存する

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レーン実行タスク：レビュー修正の履歴統合の基準を適用する前 | `exec-subagent-history.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-history.md` |
| 実行レビュー担当の起動とレビュー修正：起動方法別のレビュー基準の基準を適用する前 | `exec-review-parent-launch.md` | 判断 | `read:agent-toolkit:*/share/exec-review-parent-launch.md` |
