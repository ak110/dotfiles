# 成果物へ書く事実の調査

品質基準の正式な提供元は`agent-basics:writing-standards/references/investigation.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「QとC・Dの区別」`、`agent-basics:bugfix/references/root-cause-remediation.md#「類似見直し」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」`、`section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」` |

## 裏付け手段の回復と前提の相違

- 依頼が参照する外部資料を代替手段でも取得できない場合は、`agent-toolkit:user-confirmation-and-report`の`references/pending-decisions.md`「未確定判断の保留と暫定判断」に従う。ユーザーのみがその資料を所持している場合は、取得できなかった範囲を示して必要な情報の提供を依頼する。
- 依頼の前提条件が事実と異なっていることが判明した場合は、調査をそのまま続行せず、判明した時点で速やかに報告する。

- 既存の規範の条文やその前提（ホスト機能や実行環境の挙動など）と異なる結果を観測した場合は、公式の一次資料に明示されているか、実機で再現できるかを確認し、既存の条文が失効しているかを判定する。失効を確定できた場合は、調査したエージェントが`agent-basics:bugfix`を起動し、`agent-basics:bugfix/SKILL.md`「問題を見つけたときの対処」に従ってその場で是正するか改訂を提案する。ユーザー確認するのは、確定に必要な観測がユーザーの環境や資料でしか得られない場合と、是正内容が`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「認可を要する操作」に該当する場合に限る。その際は、観測した事象、対象条文の記載箇所、再検証手段を添えて報告する。失効を確定できない場合は条文を維持し、相違内容と再検証手段を`agent-toolkit/rules/01-agent.md`「完遂と先送り」の手順に沿ってWIとして残す。
