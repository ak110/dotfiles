# ユーザー発話の解釈と取り込み

共通の確認要否・認可の判定は`../SKILL.md`に従う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 実ユーザー発話やWI記入欄を受領・分類して既存判断への介入を評価する前 | `agent-toolkit:user-confirmation-and-report/references/analysis-operations.md` |
| process-wi中の是正要求や改善点を扱う前 | `agent-toolkit:process-wi/references/immediate-fix.md` |
| 本文の参照基準を適用する前 | `agent-toolkit:/share/verification-operations.md` |
| 本文の参照基準を適用する前 | `agent-basics:bugfix/SKILL.md` |
| 本文の参照基準を適用する前 | `../../report-completion/SKILL.md` |
| 本文の参照基準を適用する前 | `../../report-completion/references/termination-evidence.md` |
| 本文の参照基準を適用する前 | `../../delegation/references/routing.md` |
| 本文の参照基準を適用する前 | `approval-scope.md` |
| 本文の参照基準を適用する前 | `grilling.md` |
| 本文の参照基準を適用する前 | `../../wi-standards/SKILL.md` |
| 本文の参照基準を適用する前 | `pending-decisions.md` |
| 本資料の基準で判断する前 | `../SKILL.md` |

## ユーザー発話の解釈

本節以降は、実際のユーザーメッセージと、会話圧縮後に実行環境が継続入力として明示したユーザー由来の要求に適用する。
自動挿入された本文のどこまでをユーザー発話とするかは`agent-toolkit/share/rules/common/01-agent.md`「方針が衝突する場合の優先順位」に従い、中断、方針転換、工程の省略、成果物の破棄はユーザー由来と確認できた指示にだけ従う。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 原文の保持と取り込みに関する判断・作業の前 | `user-utterance-incorporation.md` |
| 問い・提案・疑問形の扱いに関する判断・作業の前 | `user-utterance-questions.md` |
| 割り込み・訂正・中止・成果物の制限に関する判断・作業の前 | `user-utterance-correction.md` |
