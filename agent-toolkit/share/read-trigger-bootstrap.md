# 操作を契機とする共有資料

操作に伴って共通で起動するスキルの宣言を本表にまとめる。各スキル固有の追加資料は、それぞれのスキル内の読込表で宣言する。
配送操作の語彙と記法は、`read-trigger-contract.md`「表と取得方法」の定義に従う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

## 操作で起動する共有スキル

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲操作の前 | `../skills/delegation/SKILL.md` | 操作 | `delegation` |
| 検索の前 | `../skills/search/SKILL.md` | 操作 | `search` |
| 原因分析の記述の前 | `../skills/bugfix/SKILL.md` | 操作 | `root-cause-writing` |
| 個別の一時領域の作成の前 | `../skills/managed-temp/SKILL.md` | 操作 | `managed-temp-create` |
| 本文の作成・更新の前 | `../skills/writing-standards/SKILL.md` | 操作 | `body-writing` |
| 検証の実行の前 | `../skills/check-execution/SKILL.md`、`../skills/check-execution/references/verification-scope.md` | 操作 | `verification` |
| 要件・技術判断を分類し、確認要否を判定する前 | `../skills/user-confirmation-and-report/references/approval-scope.md` | 判断 | `skill:user-confirmation-and-report` |
| 確認本文と選択肢を起草する前 | `../skills/user-confirmation-and-report/references/approval-scope.md`、`../skills/user-confirmation-and-report/references/choice-construction.md` | 判断 | `confirmation` |
| 原因分析の判断前と記述前 | `agent-basics:bugfix/references/root-cause-analysis.md`、`../skills/bugfix/references/root-cause-analysis.md`、`../skills/bugfix/references/analysis-records.md` | 判断 | `skill:bugfix`、`root-cause-writing` |
| エージェント向け文書の編集前 | `agent-basics:writing-standards/references/writing.md`、`agent-basics:writing-standards/references/agent-documents-basics.md`、`../skills/writing-standards/references/agent-documents-basics.md#「運用条件」「置き場所と配送」「読込表の宣言」` | 操作 | `agent-document-writing` |

## 常時規範が名指しする資料

常時配送される規範の選択基準に基づいて参照する資料の取得方法を、共通の宣言としてここにまとめる。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 配送操作の語彙と記法を選ぶ前 | `read-trigger-contract.md#「表と取得方法」` | 判断 | `section:agent-toolkit:*/share/read-trigger-contract.md#「表と取得方法」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/codex-runtime.md#「後続操作の共通先行条件」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/codex-runtime.md#「後続操作の共通先行条件」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/runtime-routing.md#「実行手段」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/choice-construction.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/choice-construction.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/waiting-and-monitoring.md#「待機区間の構成」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#「待機区間の構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/codex-format.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/codex-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/references/user-utterance.md#「成果物の制限」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「成果物の制限」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/SKILL.md#「継続と新規起動」` | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#「継続と新規起動」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/receiving.md#「受領後の扱い」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/receiving.md#「受領後の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.parent.md#「レーン担当が確定した質問への回答」` | 判断 | `section:agent-toolkit:*/share/exec.parent.md#「レーン担当が確定した質問への回答」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/receiving.md#「返却形式の適合確認」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/receiving.md#「返却形式の適合確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/routing.md#「要否判定」「委譲単位の確定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「要否判定」「委譲単位の確定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/subagent-queue-ops.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/subagent-queue-ops.md` |

読込経路の取得時分類と廃止前の本文配送の観測記録は、`docs/development/audit-records.md`「agent-toolkit/share/read-trigger-bootstrap.md：操作を契機とする共有資料：2026年10月27日」にある。
