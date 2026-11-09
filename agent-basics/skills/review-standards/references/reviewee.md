# レビューイー側心得

<!-- 節をまたぐ手順を通し番号で示す。 -->
<!-- markdownlint-disable MD029 -->

本書は、レビュー指摘、改善提案、ユーザーからの割り込みや是正要求、作業中の想定外の発見（以下、是正要求等）を受け取ったエージェントと、外部の記事や資料から自分で改善案を構成するエージェントに向けて、採否の判断、修正の必要性の立証、安全な修正と検証、恒久ルールへの反映判定を進めるための基準を定める。
作業は`reviewee-acceptance.md`「ユーザー指摘の受領」の手順1から進める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 指摘前の判断記録と比較する前 | `exec-review-recording.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/exec-review-recording.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-analysis-initial.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-initial.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/design-heuristics-proportionality.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-proportionality.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/quality-principles-qcd.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/quality-principles-qcd.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `judgment-details-feasibility.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/judgment-details-feasibility.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 調査の停滞や修正による症状・回避処理の増加に対処する前 | `../../bugfix/references/root-cause-scenarios-recurrence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-scenarios-recurrence.md`、`../../bugfix/references/root-cause-remediation.md`、`../../bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/review-only-criteria.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/review-only-criteria.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レビュー指摘の位置付け・ユーザー指摘の受領・指摘発生時の扱い・改善効果を指定する要求の扱いに関する判断・作業の前 | `reviewee-acceptance.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee-acceptance.md` |
| 同じ指摘の反復に関する判断・作業の前 | `reviewee-recurrence.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee-recurrence.md` |
| 修正要否の立証・安全な修正・恒久ルールへの反映判定に関する判断・作業の前 | `reviewee-correction.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee-correction.md` |
| 公開可能性の検証・方式見直しと再レビューに関する判断・作業の前 | `reviewee-verification.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee-verification.md` |
