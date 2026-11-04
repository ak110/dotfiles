# Agent Skills実装ガイドライン

品質基準の正式な提供元は`agent-basics:writing-standards/references/agent-skills.md`である。呼び出し元の条件と節指定を引き継ぎ、本書の運用条件と正式な資料の必要節・前提・例外を読む。両方に同名の見出しがあれば、その範囲を両方で確認する。範囲の選択と取得済み本文の再利用は`../SKILL.md`「名指し参照の読込方法」に従う。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-skills.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |

## スキル間操作

- ユーザーへの完了報告の内容や形式は`agent-toolkit:report-completion`に従う。個別スキル内では同スキルの起動を指示するにとどめる。個別スキル側で報告内容を完結させてしまうと、工程がそこで終了し、共通スキルの起動が行われなくなるためである。

## ユーザーが起動するスキルの着手手順

`user-invocable: false`が設定されていないスキルで着手前に確認したい事項がある場合は、実際に必要になった時点で尋ねるか、デフォルトの動作を採用して事後報告で伝える。
