# PowerShell記述スタイル

品質基準の正式な提供元は`agent-basics:writing-standards/references/powershell.md`である。
呼び出し元の読込条件を引き継ぎ、本書の運用条件と条件に該当する正式な資料の全文を読む。
両方に同じ主題があれば、両方の全文を確認する。
範囲の選択と取得済み本文の再利用は`agent-basics:writing-standards/references/read-method.md`に従う。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/powershell.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/powershell.md` |

## 運用条件

- agent-toolkitプラグインは、.ps1ファイルへ改行コードをLFのみで書き込もうとすると警告を返すが、書き込み自体は正常に成立する。
