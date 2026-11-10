# 場面別の原因分析

個別状況を調べるエージェントが、現在の事象に該当する判断を選ぶ。
共通の初動で確定した期待契約・直接的原因を用い、根本原因や重大度の再判定が必要な場合はその段階へ戻る。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/judgment-details.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/judgment-details.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../check-execution/references/diagnostics-results.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics-results.md` |
| 本文の条件に従い「前提の再検討」を判断する前 | `root-cause-severity-quality.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-quality.md` |
| 本文の条件に従い「再発防止策の必須性」を判断する前 | `root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文の条件に従い参照先の基準を使う前 | `../../writing-standards/references/testing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 規範の適用と目的の成立・反復した指摘の原因判定・同一症状の修正反復に関する判断・作業の前 | `root-cause-scenarios-recurrence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md` |
| 情報探索の問題・反復する加工の問題・母集団と件数を数える層に関する判断・作業の前 | `root-cause-scenarios-investigation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-investigation.md` |
| テストの原因調査・所要の原因調査に関する判断・作業の前 | `root-cause-scenarios-testing.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-testing.md` |
| 外部からの不具合報告・作業途中で発見した事象に関する判断・作業の前 | `root-cause-scenarios-reports.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-reports.md` |
