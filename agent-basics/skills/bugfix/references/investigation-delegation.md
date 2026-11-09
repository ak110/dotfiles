# 独立した調査を依頼・検収する際の品質

別の担当者やサブエージェントに独立した調査を依頼する際は、発生した事象、本来あるべき仕様（期待契約）、実際の観測結果、発生条件、ログや関連資料、対象のバージョン、および再現手順を伝える。
あらかじめ原因の候補や修正案を伝えてしまうと、相手の思考がその仮説の検証だけに狭まってしまう可能性があるため注意する。
調査先から返された推定結果や対策案はあくまで候補の1つとして扱い、直接的な原因、根本原因、影響を受ける母集団、必要な処置について、実際のコードや実行結果をもとに自身で独立して再確認する。
分析の深さにかかわらず、調査結果の妥当性は、初動・根本原因と重大度・類似見直し・処置に該当する段階の基準で確認する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 調査結果の期待契約・直接的原因を検収する前 | `root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |
| 調査結果の根本原因・重大度を検収する前 | `root-cause-severity.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md` |
| 調査結果の類似見直しを検収する前 | `root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 調査結果の対策・影響範囲・階層を検収する前 | `root-cause-actions.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions.md` |
| 調査結果の再発防止策・完了を検収する前 | `root-cause-prevention.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention.md` |
