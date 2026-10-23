# 読込の配送宣言

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| エージェント向け文書（AGENTS.md、CLAUDE.md、ルール、SKILL.md、サブエージェント定義、referencesなど）の編集前 | `references/writing.md`の「文章の共通規定」「読み手別の追加注意点」「構成・構造」「日本語の書き方」「改訂・保守」と`references/agent-documents-basics.md` | 操作 | `agent-document-writing` | |
| スキル編集 | `references/agent-skills.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills.md` | |
| スキル編集 | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| エージェント向け文書の置き場所を選ぶ時 | `references/agent-documents-basics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md` | |
| サブエージェント定義ファイルの編集、およびサブエージェントが関与する手順の作成・改訂 | `references/sub-agents.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/sub-agents.md` | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/claude-hooks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks.md` | |
| hookスクリプトの新設・変更（入出力、matcher、環境変数） | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| 遮断・警告を返すhookの判定を新設・変更する時と、hookを置くか、遮断と警告のどちらにするかを判定する時 | `references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` | |
| hookの出力フィールドを選ぶ時と、`PermissionRequest`・`UserPromptSubmit`のhookを新設・変更する時 | `references/claude-hooks-output.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-output.md` | |
| `Stop`・`SubagentStop`のhookと、終了工程の証拠を新設・変更する時 | `references/claude-hooks-stop.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-stop.md` | |
| 遮断・警告・定型の通知を出力するhookを新設・変更する時と、hookなどの生成元がエージェントへ直接渡す本文（記述言語と`atk-auto`の標識）を新設・改訂する時 | `references/claude-hooks-messages.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md` | |
| auto modeのカスタムルール編集 | `references/auto-mode.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/auto-mode.md` | |
| auto modeのカスタムルール編集 | `references/host-official-references.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/host-official-references.md` | |
| 機械チェックスクリプトの新設・改修 | `references/check-script-design.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/check-script-design.md` | |
| エージェント向け文書へ新しい規定を追記する時と、文書の記述量を管理する時 | `references/agent-documents-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md` | |
