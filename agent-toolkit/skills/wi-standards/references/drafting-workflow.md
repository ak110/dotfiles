# WIの調査・起草・保存の共通手順

通常AWIと報告用以外のUWIに適用する。
`agent-toolkit:report-completion`が定義する報告用UWIの確認と保存は、同スキルの`../../report-completion/references/report-formats-uwi.md`「報告用UWIの確認と保存」と`uwi-format.md`に従う。

作成主体は専念型メイン（WI作成専用セッションのメイン）、文脈分離のための専念型担当、他の作業中に投入する付随型担当である。
WI作成専用では件数によらずメイン自身が作業し、専念型担当の起動条件は`../../../share/add-wi-parent-launch.md`「起動方法」に従う。
各主体はwi-standards、writing-standardsの読込表と対象リポジトリの規範を適用する。
観測した不具合・ユーザーの指摘・エラー・hookの誤動作が起点なら、調査に入る前に`agent-basics:bugfix`を起動し、`drafting-workflow-investigation.md`「前提と観測の確認」に従う。
要求の原文、出所、確定した要件、技術判断、観測の記録を明確に区別し、取得済みの証拠は再利用する。
専念型メイン自身が保持している原文や観測結果は、委譲用の入力として再構成せず、元の記録をそのまま使用する。
投入先リポジトリの確定、既存項目の保留（hold）や解除（unhold）、回答済みUWIの照会は、wi-standardsの状態管理と投入契約に従って行う。
投入先リポジトリの外部にある対象については後述の処置要否判定に従い、ユーザー固有の判断が必要な事項が残る場合は`drafting-workflow-requirements.md`「未確定事項」へ渡す。
明確な基準に基づく大量の走査処理は、軽量な探索用エージェントやシェルスクリプトにまとめることを検討し、原因の特定、採否の判断、本文の起草といった中核作業は上位の作成主体自身が担う。
処理の分割はdelegationスキルの総費用判断に従って決定する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-records-conversation.md` |
| 依頼の`再発の経過を問う要求`が`該当`の場合に加え、起草者が逐語本文・趣旨から失敗・誤り・指摘の反復、頻度、悪化、いつからか守られなくなったことへの言及と判断した要求で、原因を確定する前 | `agent-basics:bugfix/references/root-cause-severity.md`と`agent-basics:bugfix/references/root-cause-scenarios.md`を全文読む | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md` |
| 原因分析の書式を使う前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 原因分析の処置を記録する前 | `agent-basics:bugfix/references/root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/add-wi-parent-launch.md` | 判断 | `read:agent-toolkit:*/share/add-wi-parent-launch.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wi-standards-origin.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-standards-origin.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-actions.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-prevention.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-scope.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `origin-and-approval-comments.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-comments.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-decisions.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-external.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-external.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/user-confirmation-and-report-decisions.md`、`../../user-confirmation-and-report/references/user-utterance.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md`、`read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `origin-and-approval-origin.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-origin.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-duplicates.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-duplicates.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `drafting-completion-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-completion.md` |
| 保存する本文の共通品質を判定する前 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` |
| エージェント向け文書を追加・変更するAWIを起草する前 | `agent-basics:writing-standards/references/agent-documents-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `awi-body-evidence.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `origin-and-approval.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval.md` |

## 起票目的別の調査出口

登録条件は`wi-standards-body.md`「起票目的別の調査出口」に従う。

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 直接起草と独立した検収の選択に関する判断・作業の前 | `drafting-workflow-review-route.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-review-route.md` |
| 出所と観測の取得に関する判断・作業の前 | `drafting-workflow-evidence.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-evidence.md` |
| 投入条件の判定・前提と観測の確認に関する判断・作業の前 | `drafting-workflow-investigation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-investigation.md` |
| 確定した要件と委譲元の判断・未確定事項に関する判断・作業の前 | `drafting-workflow-requirements.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-requirements.md` |
| エージェント向け文書の構成・本文の起草に関する判断・作業の前 | `drafting-workflow-writing.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-writing.md` |
| 保存前の確認に関する判断・作業の前 | `drafting-workflow-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-validation.md` |
| 投入に関する判断・作業の前 | `drafting-workflow-saving.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-saving.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 作業完了報告とセッションの完了処理の基準を適用する前 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| AWIとUWIの共通規範：通常AWIの本文の基準を適用する前 | `wi-standards-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-standards-body.md` |
