# PowerShell記述スタイル

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/powershell.md`である。呼び出し元が指定した条件と読み込み範囲は、同名の正式な品質資料へ引き継ぐ。節の指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来どおり全文を読み込む契約を保つ。

以下はagent-toolkit固有の運用条件である。呼び出し元の条件や読み込み範囲に応じて、この文書に残る運用節を参照する。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選定した資料の指定範囲を取得してから、その基準に基づく具体的な判断へ進む。起動時の行に示された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/powershell.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/powershell.md` |

## 運用条件

- agent-toolkitプラグインは、.ps1ファイルへ改行コードをLFのみで書き込もうとすると警告を返すが、書き込み自体は正常に成立する。
