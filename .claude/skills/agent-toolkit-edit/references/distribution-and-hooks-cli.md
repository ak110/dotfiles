# agent-toolkitの実装と配布の規約：atkの実行結果出力

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 機構や外部インターフェースの変更に伴う文書の追随先を確定する前 | `agent-documents.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agent-documents.md` |
| Claudeの子入力の寿命とserver保持版を判断する前 | `agents-server-shared-state-retention.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-retention.md` |
| hookの実装や登録を新設・変更する前 | `claude-hooks.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks.md` |
| hookの遮断・警告の判定を新設・変更する前 | `claude-hooks-block-warn.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-block-warn.md` |
| hookの出力フィールドを選択・変更する前 | `claude-hooks-output.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-output.md` |
| hook通知の本文（`fix`・次の操作を含む）またはformatterを新設・変更する前 | `claude-hooks-messages.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-messages.md` |

## atkの実行結果出力

`atk`のサブコマンドや出力メッセージの行を追加・変更する際は、接頭辞の文字列を直接記述せず、`agent-toolkit/agent_toolkit/_atk/outcome.py`で定義されている接頭辞と区分を使用する。
新規の末端（リーフ）サブコマンドを追加した場合は、同モジュールの区分テーブルにも登録する（この対応関係は`atk_help_test.py`で自動検証される）。
出力する行は、実行結果、ならびに失敗や警告の詳細および次に実行すべき操作に限定する。
リーフコマンドの登録や区分を変更した際は、子プロセスの標準出力が成功メッセージより先に出力される場合も含め、結果行が区分に応じた出力先と順序に従っていることをテストで確認する。
この設計の目的や、検討の末に採用を見送った代替案については、`docs/development/design-cli.md`の「atkの結果行の接頭辞と出力先」に記載している。
