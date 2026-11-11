# 初動と直接的原因の確認

問題を見つけたエージェントが、期待契約・実際の結果・直接的原因と拡張原因分析の要否を確定する。
根本原因、重大度、処置の詳細は、その判断へ進む時点で各資料を選ぶ。

記録の粒度は`../../writing-standards/references/writing-common.md`「文章の共通規定」に従う。
分析や検収を担当するエージェントが後から再確認できるよう、探索の手順、照会結果、比較検討や各段階での評価の過程は証拠として残す。
報告本文には最終的な結論と、詳細を確認するための参照先を記載する。

各段階の共通判断は本スキルの段階別資料を定義元とし、他工程では固有の入力、認可、記録、検収のみを定める。
記録は`analysis-records.md`、資料探索の順序と2系統4段階の分析は`causal-analysis.md`に従う。
拡張原因分析の実施にかかわらず各段階の基準を適用する。
同じ原因の処置の統合と異なる原因の切り分けは、`../../writing-standards/references/design-heuristics-screening.md`「設計案の予備選別」に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 本文の条件に従い「QとC・Dの区別」「前提の再検討」を判断する前 | `root-cause-severity-quality.md`、`root-cause-severity-impact.md` |
| 本文の条件に従い「処置の階層」を判断する前 | `root-cause-actions-hierarchy.md` |
| 本文の条件に従い「類似見直し」を判断する前 | `agent-basics:bugfix/references/root-cause-remediation.md` |
| 本文の条件に従い「再発防止策の必須性」「類似見直しと再発防止の完了」を判断する前 | `root-cause-prevention-requirements.md`、`root-cause-prevention-verification.md` |
| 本文の条件に従い「反復した指摘の原因判定」を判断する前 | `root-cause-scenarios-recurrence.md` |
| 本文の条件に従い参照先の基準を使う前 | `../../writing-standards/references/quality-principles-source-consistency.md` |
| 文書・規範・出力メッセージを変える対策を提案する前 | `../../writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/writing-common.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/design-heuristics-screening.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 問題を見つけたときの対処・初動と拡張原因分析の判定に関する判断・作業の前 | `root-cause-analysis-initial.md` |
| 直接的原因と事象時点の確定に関する判断・作業の前 | `root-cause-analysis-evidence.md` |
