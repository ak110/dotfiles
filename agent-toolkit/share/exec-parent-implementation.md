# レーン担当の起動と受領：実装完了の受領

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| レビュー修正で新たな操作・機構・副作用を採用する前 | `exec-parent-planning.md` | 判断 | `read:agent-toolkit:*/share/exec-parent-planning.md` | |
| AWIの形式を判断する前 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` | |
| 計画準備の返却を受領し、要求単位の由来の扱いを確かめる時 | `exec-subagent-inputs.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-inputs.md` | |
| 要求内の計画外編集と要求を超える変更を区別する時 | `exec-subagent-implementation.md` | 判断 | `read:agent-toolkit:*/share/exec-subagent-implementation.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `usability-review.parent.md` | 判断 | `read:agent-toolkit:*/share/usability-review.parent.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-parent-correction.md` | 判断 | `read:agent-toolkit:*/share/exec-review-parent-correction.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-parent-existing-defects.md` | 判断 | `read:agent-toolkit:*/share/exec-review-parent-existing-defects.md` | |
| 統合の返却で検証結果の警告の判定を受領する時 | `lane-integration-subagent-operation.md` | 判断 | `read:agent-toolkit:*/share/lane-integration-subagent-operation.md` | |
| 統合を指示する前に統合時の完成条件判定を行う時 | `exec-review-recording-integration.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-integration.md` | |
| レーン担当の統合の返却が、rebase進行中の競合か、統合先との組合せだけで生じた変更範囲の検証失敗の是正commitを示すとき | `agent-toolkit:process-wi`の`references/integration-failure-receipt.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/integration-failure-receipt.md` | |

## 実装完了の受領

**3.** 実装結果を検収し、独立文脈レビューと修正の往復を収束させる。

受入シナリオにおける公開された呼び出し手段、入力値、期待される結果と実際の結果の対応関係を受け取り、検証済み、未実施、条件変更の各判断を適切に分類する。
委譲元が担当する実機での観測、質問に対する回答の保存、完成条件の変更判断については、申告を受けた時点で担当者と再開先を引き継ぎ記録へ対応付ける。

レーン担当者は、すべての実装commitを完了した後に`実装完了`、`検証結果`、`画面差分`を報告する。
後ろの2行のいずれかが欠けている報告に対しては、実行レビューへ進めずに同一threadへ再提出を求める。
完成条件証拠の提出が求められている対象では、`検証結果`に各検証コマンドの終了コード、主要な確認結果、検証時のHEADの完全OID、受入シナリオごとの結果、ならびに保存された観測ログの所在が含まれており、同様の内容が進捗ログ（計画がない場合は引き継ぎ記録）に記載されていることを確認する。
不足がある場合は同一の担当者へ差し戻す。
レビュー担当者はその記録を直接読み込み、完成条件証拠の判定は同担当者が記述する。

受領後、対象worktreeの最終実装HEADを対象として、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従い計画ごとに実行レビュー担当を起動する（計画がない場合はWIを基準とする）。
起動の直前に`git merge-base <専用branch> <ベースbranch>`を実行して共通祖先commitを特定し、`開始時のHEAD`として渡す。
`画面差分: あり`の場合は、同一工程内で`${CLAUDE_PLUGIN_ROOT}/share/usability-review.parent.md`に従いユーザビリティレビュー担当も起動する。
実行レビューおよびユーザビリティレビューからの修正要求は、同一threadのレーン担当者へ返す。
同一threadを継続できない場合は、`exec-review-parent-correction.md`「レビュー修正」に従って`担当種別`を`レビュー修正担当`とした新たなレーン担当者を起動する。
レビュー指摘に対する修正方針として新たな操作、機構、または副作用を採用した場合は、`exec-parent-planning.md`「計画準備の受領」の禁止条件との突き合わせを改めて実施する。
不適合であれば修正へ進めずに`share/review-loop-coordination.md`に沿った対話を通じて是正する。
レビューが収束した後は、委譲先から受け取ったすべての事項について確認の要否を判定し、`skills/process-wi/references/run-lanes.md`「実装とレビュー」に定める確認の発行と検収を完了する（確認不要の報告に対してUWIの起草を求めない）。
質問に対する回答の保存と返却も完了させたうえで統合処理へ進む。
各ラウンドの返却や走査記録に含まれる既存の不具合については、レーン経由で報告されたものも含めて`exec-review-parent-existing-defects.md`「返却に含まれる既存不良の受領」へ接続して処理する。
統合を指示する前に、対象ごとの処置内容が引き継ぎ記録に正しく対応付けられていることを確認する。
レビュー上の未解決指摘が0件であることと、既存不具合に対する処置が完了していることは、それぞれ個別に確認する。

## レーン担当が確定した質問への回答

**4.** ユーザーへ回答を届けるため、統合指示の前に最新下書きを保存して結果を返す。

WIへ直接記入された質問（AWIの`## ユーザーコメント`、UWIの`## 回答`）の回答下書き絶対パスと根拠を受領する。
下書きを実行レビュー・読者別探索へ渡し、収束後かつ統合指示前に、`agent-toolkit:user-confirmation-and-report`「手段の選択」と`agent-toolkit:wi-standards`に従い、回答ごとのUWIへレビュー済み最新本文を保存する。
`実装なし`と`マージなし`のレーンでは、`agent-toolkit/share/rules/common/01-agent.md`「事実と推論」の基準に沿って、下書き内の断定的な主張を根拠ならびに現物と突き合わせて確認したうえで保存し、確認が取れない主張はレーン担当者へ差し戻す。
保存したUWIのファイル名を同一のレーン担当者へ返し、計画が存在する場合は進捗ログへ、計画がない場合は引き継ぎ記録へ記録させる。
保存に失敗した場合は原因を解消したうえで、実在するファイル名を通知する。

## エスカレーションの受領

**5.** 途中で判断要請を受けたら事由を分類して必要な回答を返し、該当する受領・検収の手順へ戻る。

作業を続行できない理由を受け取った場合は、`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## エスカレーションの事由別の扱い`に従って事由を分類し、必要な回答のみを同一threadへ返信する。
完成条件の変更や除外に関する判断要請は、同節の該当項目および同書「技術判断で外せる完成条件」の定めに従って処理する。
統合結果の返却がrebase作業中の競合を示している場合や、統合先との組み合わせによってのみ発生した検証失敗に対する是正commitを示している場合は、読込表の`integration-failure-receipt.md`に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 実行レビュー反復の調整の基準を適用する前 | `review-loop-coordination.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination.md` |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
