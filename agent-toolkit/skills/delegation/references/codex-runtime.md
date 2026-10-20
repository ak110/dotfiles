# Codexでの委譲方法

## ツール名の読み替え

agent-toolkitの文書に現れるClaude Codeのツール名は、Codexで次の責務へ読み替える。

| Claude Code | Codex相当 |
| --- | --- |
| `Agent` | `routing.md`「会話を引き継ぐ委譲」がforkを選ぶ場合は`spawn_agent`、それ以外は`runtime-routing.md`に従い`agents_server`へ委譲する |
| `SendMessage` | 起動手段が返した識別子と継続操作を使う |
| `TaskStop` | 起動手段の中断操作を使い、同じ識別子で停止を確認する |
| `ToolSearch` | 公開ツール一覧または検索機能から利用可能な能力へ分解し、必須能力がなければ差し戻す |
| `Monitor` | 起動手段の状態確認と待機結果を使う |
| `AskUserQuestion` | 公開された構造化質問を使い、利用できなければ`agent-toolkit:user-confirmation-and-report`のCodex表示形式に従う |
| `Skill` | descriptionまたは明示起動で選び、`SKILL.md`を全文読む。出力隔離には`agents_server`を使う |
| `Read`・`Write`・`Edit` | Codexのネイティブ編集機能を使う |
| `Bash`・`Grep`・`Glob` | シェル経由のネイティブ機能を使う |
| `WebFetch`・`WebSearch` | CodexのWeb機能を使う |
| `EnterPlanMode`・`ExitPlanMode` | `agent-toolkit:plan-mode`の`references/codex-runtime.md`に従う |
| `ScheduleWakeup`・`CronCreate`・`CronList`・`CronDelete` | 公開能力がなければ、手動運用またはユーザーへの依頼へ切り替える |

Codexネイティブ委譲は`spawn_agent`で起動し、`send_message`は稼働中の入力追加、`followup_task`は待機中か終端後の同一エージェントの継続、`wait_agent`は終端待機、`interrupt_agent`は自身が起動したエージェントの処理の中断に使う。`fork_turns`で渡す会話履歴と、`SubagentStart` hookが追加する`rules-subagent.md`は別の入力であり、forkしない場合も共通委譲先規範はhookから、`AGENTS.md`は対象worktreeの自動読込から届く。Codex固有の委譲先規範が必要になれば、共通規範と別ファイルに置き同じhook生成でCodexだけへ追加する。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月21日」にある。

forkを選ぶときは`spawn_agent`の`fork_turns`を省略するか`"all"`にする。全履歴を渡す起動は親のモデルとreasoning effortを継承し、上書きを受け付けない。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月27日」にある。

## agents_serverの起動

`agents_server`の`start`（全`mode`）はCodexネイティブ委譲とは別の仕組みで、`model_type`からengine、model、effortを解決する。通常起動（`task`の通常起動と`delegate`）には`_agents_server/launch_prompts.py`が`rules-subagent.md`をsystem promptへ加える。軽量な探索・書込・shell実行へ届く規範は`agent-toolkit:writing-standards`の`references/delivery-scope.md`の配送範囲表が示し、届かない制約は委譲プロンプトが持つ。
起動時の可用性失敗による候補切替はサーバーへ委ね、委譲元は1回の起動応答を受け取る。
起動後のturnで可用性失敗により終端した場合は、前任の終端と書込所有権の解放を確認し、結果の`next_action`に従って`start`へ`handoff_from`だけを指定して引き継ぎ起動する。
サーバーが前任の起動条件と記録を後任へ渡し、応答の後任`session_id`を待機対象にする。
引き継ぎ起動の定義と`Agent`の代替起動で渡す記録は`runtime-routing.md`が定める。
継続は`send_message`、中断は`kill`、破棄は`stop`、結果受領は`atk agents wait`を使う。出力量の大きいコマンドは`start`の`shell`、読取専用探索は`explore`を、agents_serverのserver instructionsが示す採算の目安に従って使う。

工程別モデル設定のキーを持つ工程は`runtime-routing.md`でengineを解決する。`engine=claude`をCodexの`spawn_agent`へ置き換えず、Claudeへは対応する`model_type`で`agents_server`を使う。指定engineの実行手段がなければ未完了として返すか、続行できない理由を返す。

## 後続操作の共通先行条件

元担当は、同じ作業のために既に起動した委譲先を指す。
元担当の無い初回起動は`references/runtime-routing.md`「工程別モデル設定」に従う。client確立中か`thread/start`前に可用性失敗を観測し、状態照会でsession未生成かつ元担当不在を実際に確かめた場合（初回生成前失敗）の代替起動も初回起動として扱う。確かめられない場合と、sessionか元担当が生成された後の代替起動は次の条件に従う。

元担当の回復、置換、再起動、代替起動および`interrupt_agent`による中断は、ユーザーの明示要求、終端か失敗への遷移、タスク契約上のキャンセル指定のいずれかを確認した場合に限る。回復・置換・再起動・代替起動では元担当の終端と書込所有権の解放も確かめる。
`list_agents`が元担当を`running`と返している間はそのstatusを受け入れ、これらの操作を保留する。差分、HEAD、更新時刻、無応答、経過時間からの停滞の推定と、補助観測や催促は開始の根拠にしない。

元担当を持たないレーン担当、独立したレビュー、CI修正の起動は`references/runtime-routing.md`「工程別モデル設定」に従う。

## agents_serverの二層待機

外側セルの`cell_id`と内側コマンドの`session_id`のいずれかが未回収なら、`share/rules-common.codex.md`「Codex固有の入出力」の手段で同じ実行を回収する。
外側セルか`exec_command`のyieldは同じ実行の継続区間として扱い、代替起動・中断・巻き取りは「後続操作の共通先行条件」が成立した場合に限る。
`functions.exec`の内側で`agents_server`を起動し、`PostToolUse`がMCP応答を直接観測できない場合は、起動応答の`root_session_id`を保持して`atk agents wait --root-session-id <値>`へ渡す。CLIは状態ディレクトリの実在と確認済みのルートsessionとの一致を検証する。

`functions.exec`から`atk agents wait`を起動する場合も、`share/rules-common.codex.md`「Codex固有の入出力」のコマンド回収契約に従い、外側の`cell_id`を`functions.wait`へ渡す。
CLIへタスク固有timeoutを渡さず、CLI自身が待機上限へ達し対象が未終端なら、その結果を確かめてから新しい待機を始める。
逐次待機とlock競合した後発待機の回収は`references/waiting-and-monitoring.md`「`atk agents wait`の応答の扱い」に従う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日」にある。

委譲先の成果物側だけを補助観測するときは`atk watch --worktree [<ラベル>=]<絶対パス> --file [<ラベル>=]<絶対パス>`を単独で使い、session自体の稼働確認には起動手段の状態を使う。
