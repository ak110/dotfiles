# Codexでの委譲方法：ツール名の読み替え

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲先と起動手段を選ぶ前 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 委譲先と起動手段を選ぶ前 | `process-model-settings.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/process-model-settings.md` |
| 同一担当の継続・交代を判断する前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |
| 可用性の障害を確認して回復する前 | `fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` |

## ツール名の読み替え

Codex組み込み委譲の使用条件は`runtime-routing.md`「実行手段」に従う。
以下の操作説明にも同じ条件を適用する。

agent-toolkitの文書に登場するClaude Code向けのツール名は、Codex環境では次の相当機能に読み替えて処理する。

| Claude Code | Codex相当 |
| --- | --- |
| `Agent` | `routing.md`「会話を引き継ぐ委譲」でforkを選択する場合は`spawn_agent`を使用し、それ以外は`runtime-routing.md`「実行手段」に従って`agents_server`へ委譲する。`agents_server`を利用できない場合の組み込み代替では、`fork_turns: "none"`を指定する |
| `SendMessage` | 起動手段が返却した識別子と継続操作を使用する |
| `TaskStop` | 起動手段の中断操作を使用し、同一の識別子で停止を確認する |
| `ToolSearch` | 公開ツール一覧または検索機能から利用可能な機能へ分解し、必須機能が存在しない場合は処理を差し戻す |
| `Monitor` | 起動手段の状態確認機能と待機結果を使用する |
| `AskUserQuestion` | 公開されている構造化質問ツールを使用し、利用できない場合は`agent-toolkit:user-confirmation-and-report`のCodex表示形式に従う |
| `Skill` | descriptionまたは明示的な起動指定で選択し、`SKILL.md`を全文読み込む。出力を隔離する場合は`agents_server`を使用する |
| `Read`・`Write`・`Edit` | Codexのネイティブ編集機能を使用する |
| `Bash`・`Grep`・`Glob` | シェル経由のネイティブ機能を使用する |
| `WebFetch`・`WebSearch` | CodexのWeb機能を使用する |
| `EnterPlanMode`・`ExitPlanMode` | `agent-toolkit:write-plan`の`references/codex-runtime.md`に従う |
| `ScheduleWakeup`・`CronCreate`・`CronList`・`CronDelete` | 公開機能が存在しない場合は、手動運用またはユーザーへの依頼へ切り替える |

Codexネイティブ委譲は`spawn_agent`で起動する。
稼働中エージェントへの入力追加には`send_message`、待機中または終了後の同一エージェントの継続には`followup_task`、終了待機には`wait_agent`、自身が起動したエージェントの中断には`interrupt_agent`を使用する。
`fork_turns`で引き渡される会話履歴と、`SubagentStart`フックが追加する`rules-subagent.md`は別個の入力である。
forkを行わない場合でも、共通の委譲先規範はフックから渡され、`AGENTS.md`は対象worktreeの自動読込によって渡される。
Codex固有の委譲先規範が必要になった場合は、共通規範とは別のファイルに配置し、同一のフック生成処理によってCodex環境のみに追加する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月21日」に記載されている。

forkを選択する場合は、`spawn_agent`の`fork_turns`を省略するか、`"all"`を指定する。
全会話履歴を引き渡す起動では、親エージェントのモデルとreasoning effortが自動的に継承され、上書き指定は受け付けられない。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月27日」に記載されている。
forkを選択しない組み込み代替では、`fork_turns: "none"`を明示し、必要な委譲入力を引き渡す。
