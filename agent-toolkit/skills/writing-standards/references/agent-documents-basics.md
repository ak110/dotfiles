# エージェント向け文書品質（基本）

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/agent-documents-basics.md`である。呼び出し元が指定した条件と読込範囲は、同名の品質資料へそのまま引き継ぐ。節の指定がある場合は移設先の実見出しを参照し、指定がない場合は全文を読む契約を維持する。

以下はagent-toolkitが所有する運用条件である。呼び出し元の条件と読込範囲に従い、本書に残る運用節を参照する。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料の指定範囲を取得した上で、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

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

- 役割別の規範は、想定する読み手と読み込む契機に応じて配置する。全エージェント共通の規定は`rules/`、メインエージェント限定の規定は`share/rules-main.md`、委譲先限定の規定は`share/rules-subagent.md`に配置し、ホスト固有の差分は同じ接頭辞を持つホスト別ファイルへ置く。Claude CodeやCodexのhook、agents_serverの通常委譲、ならびに生成manifestがこれらを配送するため、常時ロードと同等の拘束力を持つ。委譲元と委譲先の双方が守るべき受け渡し規定は全エージェント共通とし、片側のみに適用する規定は読み手別のファイルへ分ける。軽量委譲へ共通規範を配送しない場合は、軽量委譲の起動契約に必要な制約を記述する。agent-toolkitが読み手へ常に配送する規範（`rules/`配下、および読み手に応じて配送する`share/rules-main*.md`、`rules-common.codex.md`、`rules-subagent*.md`）を常時規範と呼ぶ。

- 読込の階層: 常時ロードは`rules/`とfrontmatterの`description`、役割別の注入は`share/`配下の`rules-`で始まるファイル、配送文は`share/agents-server-*.md`である。メイン起動時は`SKILL.md`本体、役割起動時は`share/<役割名>.parent.md`および`share/<役割名>.subagent.md`、明示的なRead時は`references/`、サブエージェント注入はagent定義とfrontmatterの`skills`が該当する。明示的なRead時を除き、読み手が該当する作業場面に入っていなくても起動時に全文を読み込む。

- 各起動区分のエージェントへどの文書が届くかは、`delivery-scope.md`の配送範囲表で確認する。

## 読込表の宣言

読込表の契機、配送操作、資料選択子は`agent-toolkit:/share/read-trigger-contract.md`に従って宣言する。条件と明示した節の範囲を守り、判断前の到達手順と確定操作への補完配送を区別して記述する。
