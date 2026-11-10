# 報告のフォーマット

本書は、`agent-toolkit:report-completion`が出力する作業完了報告、振り返り結果報告、AWI投入結果報告、および自律モードで投入する報告用UWIの本文書式を定める。
作業完了報告の送達は同スキルの`SKILL.md`「工程」、振り返りの報告は`agent-toolkit:review-session`と`agent-toolkit:review-session batch`の工程に従う。

これら4種類の報告は、ユーザーが結論と判断に必要な要点（作業結果、未達事項、対応状況）を簡潔に把握できるように作成する。
報告の内容や書式はStop hookによる自動判定の対象外であるため、報告を作成するメインエージェント自身が、送達前に本書の書式と照らし合わせて確認する。
原因分析や調査の経緯、AWIのH1タイトルと重複する概要などは本文中に展開せず、AWIや証拠記録側に残す。
一方で、確定済みの観測根拠、未確定の照会・再現状況、理由が残っている事項、および未達事項は要約して報告に残す。
送達前に各文が加える情報を確かめ、依頼や起動スキルから既知の工程説明を省く。
登録成功時の後続実装の説明もこの基準で扱い、未達・失敗・制約・予想との差は保持する。
ユーザーによる対応が必要な未完了事項については、`agent-toolkit:user-confirmation-and-report`「手段の選択」に沿って個別UWIの登録要否を判定し、保存した場合は既存の見出し（`### 投入したWI`など）でその所在を明記する。
自明な取得・再実行・実機確認の手順案内は、必要に応じて成果や検証結果に添える。
各報告の見出しは規定の構成を使用し、ユーザーの判断や行動を促すための独自の追加見出しは設けない。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-session/references/batch.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/batch.md` |
| 振り返りの時間目標と改善見込みを報告する前 | `agent-toolkit:review-session/references/duration-analysis.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/duration-analysis.md` |
| add-awi-by-userの投入作業の作業完了報告を作る前 | `agent-toolkit:add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/communication-methods-delivery.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods-delivery.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/check-execution-operations-launch.md`、`../../../share/check-execution-operations-output.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md`、`read:agent-toolkit:*/share/check-execution-operations-output.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/user-utterance-incorporation.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance-incorporation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-session/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-session/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-session/references/analysis-inputs.md`、`../../review-session/references/analysis-triage.md`、`../../review-session/references/analysis-actions.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-inputs.md`、`read:agent-toolkit:*/skills/review-session/references/analysis-triage.md`、`read:agent-toolkit:*/skills/review-session/references/analysis-actions.md` |
| 未検証の仮説を理由に対策を見送った判断を書く前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-session/references/analysis-cost.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-cost.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:check-execution/SKILL.md` | 判断 | `read:agent-basics:*/skills/check-execution/SKILL.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 検証結果と実行記録・検証記録の引き渡しに関する判断・作業の前 | `report-formats-verification.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-verification.md` |
| 作業完了報告のフォーマットに関する判断・作業の前 | `report-formats-completion.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-completion.md` |
| 振り返り結果報告のフォーマット・AWI投入結果報告のフォーマットに関する判断・作業の前 | `report-formats-retrospective.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-retrospective.md` |
| 報告用UWIのフォーマット・報告用UWIの確認と保存に関する判断・作業の前 | `report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| ユーザー確認とユーザーへの報告の基準を適用する前 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
