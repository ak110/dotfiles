# 実行レビュータスク：再レビュー

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 本文の起動指定に従って作成規範を取得する前 | `agent-basics:writing-standards/SKILL.md` | | | |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` | | | |
| 条件表を検収する前 | `exec-review-recording-contract.md` | | | |
| 初回レビューに入る前 | `exec-review-criteria.md` | | | |
| 再レビュー・引き継ぎ再レビューに入る前 | `exec-review-criteria.md` | | | |
| 文章成果物の初回レビュー・再レビュー・引き継ぎ再レビューで、読者別探索の起動または省略を判断する前 | `${CLAUDE_PLUGIN_ROOT}/share/reader-fit-review.parent.md` | | | |
| 差分を判定する前 | `agent-basics:writing-standards`が変更ファイルの成果物種別ごとに定める資料 | | | |
| テストコードを含む差分を判定する前 | `agent-basics:writing-standards/references/testing.md`、`agent-toolkit:commit/references/testing.md` | | | |
| `計画`を受領し、条項を生成する前 | `../skills/write-plan/references/plan-file-standards.md`、`../skills/write-plan/references/plan-file-standards-structure.md`、`../skills/write-plan/references/plan-file-standards-scope.md`、`../skills/write-plan/references/plan-file-standards-requirements.md`、`../skills/write-plan/references/plan-file-standards-design.md`、`../skills/write-plan/references/plan-file-standards-verification.md` | | | |
| `計画`を受領し、計画が過去の契約へ戻す変更、過去の契約を撤去する変更、または保持・不変性・完全復元の契約を含む場合 | `agent-toolkit:write-plan`の`references/plan-restoration-contracts.md` | | | |
| 複数のWIを`atk wi show`で取得する前 | `agent-toolkit:wi-standards`の`references/wi-show-bulk.md` | | | |
| 再発防止セルフチェックの観点を収集する前と、担当を起動・受領する前 | `recurrence-self-check.parent.md` | | | |
| 指摘を登録するか完成条件証拠を書く直前 | `agent-basics:review-standards/SKILL.md`、`exec-review-recording-issues.md`、`exec-review-recording-evidence.md`、`exec-review-recording-grounds.md` | | | |
| 着手時 | `exec-review-criteria.md`、`exec-review-criteria-execution.md` | 起動時 | `role:exec-review` | |
| 初回レビューで種類7の作成規範を点検する前 | `agent-basics:writing-standards/references/review-only-criteria.md` | | | |
| 操作や検証手段を選ぶ前 | `agent-toolkit:/share/exec-review-operations.md` | | | |
| レビュー工程の判定・記録を始める前 | `review-table-operations.md` | | | |
| レビュー工程の判定・記録を始める前 | `exec-review-criteria.md` | | | |
| レビュー工程の判定・記録を始める前 | `exec-review-recording.md`、`exec-review-recording-verdicts.md` | | | |

## 再レビュー

**2.** 再レビューの追送時は前回判定と比較元を復元し、手順3〜6を今回の範囲で行う。
初回は手順3へ進む。

再レビューでは、同一sessionの管理表、開始時点の短縮OID、レビュー基準、および`review_contract`を引き続き使用する。
委譲元から追送される入力は、`再レビューせよ`という指示、`round`、ならびに任意指定の`review_contract`と`rewrite_map`である。
確認の対象は、前回指摘に対する修正内容、前回レビューHEADから今回HEADまでの修正差分、および変更された識別子や契約に伴う直接影響範囲とする。
無関係な範囲外の既存不良の新規探索・指摘は重大性によらず初回にまとめ、再レビューは次の既報追跡と修正差分の確認に限る。
初回に報告済みの根拠補充・割当確認・未完了処置の追跡と、修正差分や直接影響範囲の欠陥・退行を確認する。
担当交代や履歴の書換えでも同じレビュー単位のラウンドを引き継ぐ。
修正差分が新たに変えた識別子・契約・出力も、初回と同じく`exec-review-subagent-exploration.md`「判定の観点と並行する探索」の種類8の走査に従って直接影響範囲を独自に算出し、提出された探索記録と検証結果に欠けている利用側テストを判定する。
修正差分の比較元には前回レビュー時のHEADを用い、返却情報における`レビュー開始時点`には初回から引き継いでいる開始時点を記載する。
Git履歴が書き換えられた後の開始時点には、書き換え後の対応するOIDを使用する。
履歴書き換えが行われた後は、前回と今回の開始時点・HEAD、ならびに各時点の契約に基づいてファイルごとの差分を比較する。
管理表の応答欄に設計・実装方式の全面的な置換が記録された直後の再レビューに限り、開始時点からの全差分を再確認の対象とする。
既存の各指摘については、現在の成果物、管理表の応答、検証結果をもとに判定し、不備が解消した行のみを「解決済み」へ更新する。
修正に伴って新たに発生した違反は、新規の指摘として登録する。
HEADが前回と同一であっても、現在の入力内容に基づいて判定する。

`rewrite_map`を受け取った場合は、判定作業に入る前に、前回確認版の開始時点・HEAD、ならびに前回の判定行や検証結果が参照しているcommitを対応表に従って新しいOIDへ読み替える。
元の取得版と異なる版で判定をやり直す行については、`exec-review-recording-grounds.md`「根拠の書き方」に従って既存の観測結果を再利用できるかを判断する。
完成条件証拠を求められた場合は全条件を新HEADで再判定する。
委譲元が前ラウンドの返却を受理した行は、修正差分・前提変化の影響がない条件だけ、`exec-review-recording-evidence.md`「受理済み判定への参照」に従って参照し、維持理由を根拠欄へ書く。
解消済み指摘を理由に`未達`・`根拠不足`としていた条件や今回影響する条件は新たな根拠で判定する。
HEAD表記だけの変更は再判定としない。
`対応完了（再レビュー不要）`で収束する場合は直前の実レビュー返却を完成条件証拠とする。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 実行レビュータスク：判定の観点と並行する探索の基準を適用する前 | `exec-review-subagent-exploration.md` |
