# 再発防止の選定と完了

再発防止策を選定・検収するエージェントが、根本原因と類似見直しの結果を用いて、候補の採否、適用範囲、有効性と完了を判断する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 再発防止セルフチェック観点の追加・保守を判断する前 | `recurrence-self-check.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/recurrence-self-check.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/writing-common.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/writing-common.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/agent-documents-additions-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions-additions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/agent-documents-basics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/design-heuristics-mechanisms.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-mechanisms.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/testing-conditions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-conditions.md` |
| 本文の条件に従い「直接的原因と事象時点の確定」を判断する前 | `root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |
| 本文の条件に従い「QとC・Dの区別」「原因分析の品質確認」を判断する前 | `root-cause-severity-quality.md`、`root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-quality.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文の条件に従い「処置の階層」を判断する前 | `root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |
| 本文の条件に従い「類似見直し」を判断する前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 自動検出の成立条件を判定する前（hookを候補にする場合） | `../../writing-standards/references/claude-hooks-block-warn-eligibility.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn-eligibility.md` |
| 自動検出の成立条件を判定する前（lint・チェックスクリプト・テストを候補にする場合） | `agent-basics:writing-standards/references/check-script-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/check-script-design.md` |
| 既存の規範・手順で対応済みかを判定する前 | `../../writing-standards/references/agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 再発防止策に関する判断・作業の前 | `root-cause-prevention-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-selection.md` |
| 再発防止策の必須性に関する判断・作業の前 | `root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 類似見直しと再発防止の完了・再発防止としてのテストに関する判断・作業の前 | `root-cause-prevention-verification.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-verification.md` |
