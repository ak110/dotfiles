# Codexでの委譲経路

## ツール名の読み替え

agent-toolkitの文書に現れるClaude Codeのツール名は、Codexで次の責務へ読み替える。

| Claude Code | Codex相当 |
| --- | --- |
| `Agent` | 実際の別主体が必要な場合だけ、`runtime-routing.md`の通常経路で`agents_server`へ委譲する |
| `SendMessage` | 起動経路が返した識別子と継続操作を使う |
| `TaskStop` | 起動経路の中断操作を使い、同じ識別子で停止を確認する |
| `ToolSearch` | 公開ツール一覧又は検索機能から利用可能な能力へ分解し、必須能力がなければ差し戻す |
| `Monitor` | 起動経路の状態確認と待機結果を使う |
| `AskUserQuestion` | 公開された構造化質問を使い、利用できなければ`agent-toolkit:confirmation-and-uwi`のCodex表示形式に従う |
| `Skill` | description又は明示起動で選び、`SKILL.md`を全文読む。出力隔離には`agents_server`を使う |
| `Read`・`Write`・`Edit` | Codexのネイティブ編集機能を使う |
| `Bash`・`Grep`・`Glob` | シェル経由のネイティブ機能を使う |
| `WebFetch`・`WebSearch` | CodexのWeb機能を使う |
| `EnterPlanMode`・`ExitPlanMode` | `agent-toolkit:plan-mode`の`references/codex-runtime.md`に従う |
| `ScheduleWakeup`・`CronCreate`・`CronList`・`CronDelete` | 公開能力がなければ、手動運用又はユーザーへの依頼へ切り替える |

agents_serverの`start`、`start_explore`、`start_write`及び`start_shell`は、対応する`model_type`からengine、model及びeffortを解決する。可用性失敗時の候補切替はサーバーへ委ね、呼び出し側は同じ失敗に再起動を重ねない。継続は`send_message`、中断は`kill`、破棄は`stop`、結果受領は`atk agents wait`を使う。出力量の大きいコマンドは`start_shell`、読取専用探索は`start_explore`を使い、各ツールの採算基準に従う。

工程別モデル設定のキーを持つ工程は`runtime-routing.md`でengineを解決する。`engine=claude`をCodexの`spawn_agent`へ置換せず、CodexからClaudeへは対応する`model_type`でagents_serverを使う。指定engineの経路がなければ同書の未完了又は`needs_escalation`で返す。

## agents_serverの二層待機

`functions.exec`のような遅延実行ツールから`atk agents wait`を起動する場合、内側のCLIと外側の実行セルを別々の待機として扱う。CLIへタスク固有timeoutを渡さず、外側が`cell_id`を返した場合は同じ識別子を`functions.wait`へ渡す。外側のyieldを理由に別の`atk agents wait`を起動しない。CLI自身が待機上限へ達し対象が未終端なら、その結果を確認してから新しい待機を開始する。

委譲先の成果物側だけを補助観測するときは、`atk watch --worktree [<ラベル>=]<絶対パス> --file [<ラベル>=]<絶対パス>`を単独で使う。この出力をsession自体の稼働確認に代用しない。
