# Python記述スタイル

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/python.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選定した資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の行に示された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/python.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/python.md` |

## 標準ライブラリとログ

- agent-toolkitパッケージ内でGitを起動するときは`agent_toolkit._git.command.run`を使う（`text=True`で渡した文字列の`input`も、UTF-8のバイト列として子プロセスへ渡される）。
