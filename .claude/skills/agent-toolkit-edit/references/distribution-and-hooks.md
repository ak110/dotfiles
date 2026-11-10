# agent-toolkitの実装と配布の規約

本書は、`.claude/skills/agent-toolkit-edit/SKILL.md`の読込表から参照する資料である。
`agent-toolkit/`の実装モジュールの配置、`atk`コマンドの出力、MCPサーバーやフックの実装・登録、権限設定、配布と反映に関する規約をまとめている。
バージョン更新は`version-bump.md`、同期先の文書は`agent-documents.md`を参照する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 機構や外部インターフェースの変更に伴う文書の追随先を確定する前 | `agent-documents.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agent-documents.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:*/skills/agent-toolkit-edit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `./../../develop-dotfiles/references/testing.md` | 判断 | `read:*/skills/develop-dotfiles/references/testing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `version-bump-execution.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/version-bump-execution.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| Claudeの子入力の寿命とserver保持版を判断する前 | `agents-server-shared-state-retention.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-retention.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md` |

## hook開発資料の選択

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| hookの実装や登録を新設・変更する前 | `claude-hooks.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks.md` |
| hookの遮断・警告の判定を新設・変更する前 | `claude-hooks-block-warn.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-block-warn.md` |
| hookの出力フィールドを選択・変更する前 | `claude-hooks-output.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-output.md` |
| hook通知の本文（`fix`・次の操作を含む）またはformatterを新設・変更する前 | `claude-hooks-messages.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/claude-hooks-messages.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| agent_toolkitパッケージの配置と層に関する判断・作業の前 | `distribution-and-hooks-layers.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-layers.md` |
| atkの実行結果出力に関する判断・作業の前 | `distribution-and-hooks-cli.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-cli.md` |
| 常駐CLIと自動起動の判定入力に関する判断・作業の前 | `distribution-and-hooks-runtime.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-runtime.md` |
| MCPサーバー識別子とホスト別ツール名・プラグイン内リソースの参照書式・権限設定の配置に関する判断・作業の前 | `distribution-and-hooks-resources.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-resources.md` |
| worktreeでの編集時の注意・marketplace管理に関する判断・作業の前 | `distribution-and-hooks-deployment.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-deployment.md` |
| フック実装の配置先（個人フックと配布物）・agents_serverのツール呼び出しを判定するhook・複数hook共存時の識別子に関する判断・作業の前 | `distribution-and-hooks-hooks.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/distribution-and-hooks-hooks.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| バージョン更新の詳細手順の基準を適用する前 | `version-bump.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/version-bump.md` |
