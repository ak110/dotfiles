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

Codexネイティブ委譲は`spawn_agent`で起動し、`send_message`は稼働中の入力追加、`followup_task`は待機中又は終端後の同一主体の継続、`wait_agent`は終端待機、`interrupt_agent`は起動主体が所有する処理の中断に使う。`fork_turns`で渡す会話履歴と、Codexの`SubagentStart` hookが追加する`rules-subagent.md`は別契約である。会話履歴をforkしない場合も共通委譲先規範はhookから適用され、`AGENTS.md`は対象worktreeの自動読込経路から適用される。Codex固有の委譲先規範が将来必要になった場合は、共通規範と別ファイルに置き、同じhook生成経路でCodexだけへ追加する。

agents_serverの`start`、`start_explore`、`start_write`及び`start_shell`はCodexネイティブ委譲とは別経路であり、対応する`model_type`からengine、model及びeffortを解決する。通常の`start`は`_agents_server/state.py`が共通の`rules-subagent.md`をsystem promptへ加える。軽量な探索・書込・shell経路は共有規範を注入せず、起動文が必要な制約を持つ。可用性失敗時の候補切替はサーバーへ委ね、呼び出し側の起動は最初の1回に限る。継続は`send_message`、中断は`kill`、破棄は`stop`、結果受領は`atk agents wait`を使う。出力量の大きいコマンドは`start_shell`、読取専用探索は`start_explore`を使い、各ツールの採算基準に従う。

工程別モデル設定のキーを持つ工程は`runtime-routing.md`でengineを解決する。`engine=claude`をCodexの`spawn_agent`へ置換せず、CodexからClaudeへは対応する`model_type`でagents_serverを使う。指定engineの経路がなければ同書の未完了又は`needs_escalation`で返す。

## agents_serverの二層待機

`functions.exec`の内側でagents_serverを起動し、`PostToolUse`がMCP応答を直接観測できない場合は、起動応答の`root_session_id`を保持し、`atk agents wait --root-session-id <値>`へ渡す。明示入力をランタイム間の識別子の受渡経路とする。CLIは明示した値について、状態ディレクトリの実在と確認済みの会話rootとの一致を検証する。

`functions.exec`のような遅延実行ツールから`atk agents wait`を起動する場合、内側のCLIと外側の実行セルを別々の待機として扱う。CLIへタスク固有timeoutを渡さず、外側が`cell_id`を返した場合は同じ識別子を`functions.wait`へ渡す。CLI自身が待機上限へ達し対象が未終端なら、その結果を確認してから新しい待機を開始する。前景のCLIが本文を返した後の逐次待機は新しいrunへ進む。先行CLIが稼働中にlock競合した後発待機だけが、先行runの本文を1回回収する。

委譲先の成果物側だけを補助観測するときは、`atk watch --worktree [<ラベル>=]<絶対パス> --file [<ラベル>=]<絶対パス>`を単独で使う。session自体の稼働確認には起動経路の状態を用いる。
