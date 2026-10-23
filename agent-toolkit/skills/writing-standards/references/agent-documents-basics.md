# エージェント向け文書品質（基本）

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/agent-documents-basics.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-basics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-additions.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/agent-documents-additions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/delivery-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/read-trigger-contract.md` | 判断 | `read:agent-toolkit:*/share/read-trigger-contract.md` |

## 運用条件

規定の追記と記述量の管理は`agent-documents-additions.md`、計画ファイルの成果物契約は`agent-toolkit:plan-mode`が定める。

## 置き場所と配送

- 役割別規範は読み手と読込契機で配置する。全エージェント共通は`rules/`、メイン限定は`share/rules-main.md`、委譲先限定は`share/rules-subagent.md`、ホスト固有の差分は同じ接頭辞のホスト別ファイルへ置く。Claude CodeとCodexのhook、agents_serverの通常委譲および生成manifestがこれらを配送するため、常時ロードと同じ拘束力を持つ。委譲元と委譲先の双方が守る受け渡し規定は全エージェント共通とし、片側だけの規定は読み手別のファイルへ置く。軽量委譲へ共通規範を配送しない場合は、軽量委譲の起動契約へ必要な制約を置く。agent-toolkitが読み手へ常に配送する規範（`rules/`配下と、読み手に応じて配送する`share/rules-main*.md`・`rules-common.codex.md`・`rules-subagent*.md`）を常時規範と呼ぶ

- 読込の階層: 常時ロードは`rules/`とfrontmatterの`description`、役割別注入は`share/`配下の`rules-`で始まるファイル、配送文は`share/agents-server-*.md`である。メイン起動時は`SKILL.md`本体、役割起動時は`share/<役割名>.parent.md`・`share/<役割名>.subagent.md`、明示Read時は`references/`、サブエージェント注入はagent定義とfrontmatterの`skills`である。明示Read時を除く階層は、読み手がその場面に入っていなくても起動とともに全文を読む

- どの起動区分のエージェントへ届くかは`delivery-scope.md`の配送範囲表で確かめる

## 読込表の宣言

読込表の契機・配送操作・資料選択子は`agent-toolkit:/share/read-trigger-contract.md`に従って宣言する。条件と明示した節の範囲を保ち、判断前の到達手順と確定操作への補完配送を分ける。
