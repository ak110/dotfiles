# PowerShell記述スタイル

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/powershell.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/powershell.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/powershell.md` |

## 運用条件

- agent-toolkitプラグインは、.ps1ファイルへ改行コードをLFのみで書き込もうとすると警告を返すが、書き込み自体は正常に成立する。
