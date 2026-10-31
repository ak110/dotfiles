# Codexでの委譲方法

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/delivery-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md#%E3%80%8C%60atk%20agents%20wait%60%E3%81%AE%E5%BF%9C%E7%AD%94%E3%81%AE%E6%89%B1%E3%81%84%E3%80%8D` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#%E3%80%8C%60atk%20agents%20wait%60%E3%81%AE%E5%BF%9C%E7%AD%94%E3%81%AE%E6%89%B1%E3%81%84%E3%80%8D` |

## ツール名の読み替え

Codex組み込み委譲の使用条件は`runtime-routing.md`「実行手段」に従う。以下の操作説明にも同じ条件を適用する。

agent-toolkitの文書に登場するClaude Code向けのツール名は、Codex環境では次の相当機能に読み替えて処理する。

| Claude Code | Codex相当 |
| --- | --- |
| `Agent` | `routing.md`「会話を引き継ぐ委譲」でforkを選択する場合は`spawn_agent`を使用し、それ以外は`runtime-routing.md`に従って`agents_server`へ委譲する。`agents_server`を利用できない場合の組み込み代替では、`fork_turns: "none"`を指定する |
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

Codexネイティブ委譲は`spawn_agent`で起動する。稼働中エージェントへの入力追加には`send_message`、待機中または終了後の同一エージェントの継続には`followup_task`、終了待機には`wait_agent`、自身が起動したエージェントの中断には`interrupt_agent`を使用する。`fork_turns`で引き渡される会話履歴と、`SubagentStart`フックが追加する`rules-subagent.md`は別個の入力である。forkを行わない場合でも、共通の委譲先規範はフックから渡され、`AGENTS.md`は対象worktreeの自動読込によって渡される。Codex固有の委譲先規範が必要になった場合は、共通規範とは別のファイルに配置し、同一のフック生成処理によってCodex環境のみに追加する。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月21日」に記載されている。

forkを選択する場合は、`spawn_agent`の`fork_turns`を省略するか、`"all"`を指定する。全会話履歴を引き渡す起動では、親エージェントのモデルとreasoning effortが自動的に継承され、上書き指定は受け付けられない。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月27日」に記載されている。
forkを選択しない組み込み代替では、`fork_turns: "none"`を明示し、必要な委譲入力を引き渡す。

## agents_serverの起動

`agents_server`の`start`（全`mode`）はCodexネイティブ委譲とは異なる仕組みであり、指定された`model_type`からengine、model、effortを解決して起動する。通常起動（`task`の通常起動および`delegate`）に対しては、`_agents_server/launch_prompts.py`が`rules-subagent.md`をシステムプロンプトへ追加する。軽量な探索・書込・shell実行に適用される規範は、`agent-toolkit:delegation/references/delivery-scope.md`にある配送範囲表に記載されている。この表で配送されない制約事項は、委譲プロンプト側で記述する。
Codex環境における役割指定`task`と`delegate`では、初回起動時も含めて、呼び出し側が保持している全体作業契約の絶対パスを`whole_contract_path`へ渡す。
サーバーが役割の起動設定を解決し、通常起動の場合だけ参照を検証・配送する。
軽量役割の`task`では指定値を参照せず、契約本文・専用記録を追加しないため、呼び出し側による内部設定の判別は不要である。
自由本文の`explore`・`write`・`shell`や`handoff_from`による引き継ぎ起動には、この引数を混在させない。
既存の通常起動での引数省略は、受領済みの参照を継承できる場合に限り互換性維持として許容される。
起動時に可用性の問題で失敗した場合の候補切り替えはサーバー側に任せ、委譲元は1回の起動応答を受け取る。
起動後のターンにおいて可用性エラーにより終了した場合は、前任エージェントの終了と書込権限の解放を確認した上で、返却結果の`next_action`に従い、`start`に`handoff_from`のみを指定して引き継ぎ起動する。
サーバー側が前任の起動条件と記録を後任へ引き渡し、応答として返された後任の`session_id`を以後の待機対象とする。
引き継ぎ起動の定義や、`Agent`ツールの代替起動で引き渡す記録内容については、`runtime-routing.md`で定める。
継続には`send_message`、中断には`kill`、破棄には`stop`、結果の受領には`atk agents wait`を使用する。出力量が大きいコマンドは`start`の`shell`モード、読み取り専用の探索は`explore`モードを、agents_serverのserver instructionsに記載されている採算の目安に従って使い分ける。

工程別モデル設定のキーが定義されている工程については、`runtime-routing.md`に従ってengineを解決する。`engine=claude`の指定をCodexの`spawn_agent`へ勝手に置き換えることは行わず、Claude向けには対応する`model_type`を指定して`agents_server`を使用する。指定されたengineを実行する手段が存在しない場合は、未完了として返すか、続行不能の理由を報告する。

## 後続操作の共通先行条件

「元担当」とは、同一の作業のためにすでに起動された委譲先エージェントを指す。
元担当が存在しない初回起動は、`references/runtime-routing.md`「工程別モデル設定」に従って実行する。クライアント接続の確立中や`thread/start`の実行前に可用性エラーが発生し、状態照会によってセッションが未生成であり元担当も不在であることが実際に確認できた場合（初回生成前失敗）の代替起動についても、初回起動と同様に扱う。これらを確認できない場合、またはセッションや元担当がすでに生成された後の代替起動は、次の条件に従う。

元担当の回復、置換、再起動、代替起動、および`interrupt_agent`による中断は、ユーザーからの明示的な要求、終了または失敗への状態遷移、タスク契約上のキャンセル指示のいずれかを確認できた場合に限り実行する。回復・置換・再起動・代替起動する際は、元担当の終了と書込権限の解放が完了していることもあわせて確認する。
`list_agents`が元担当のステータスとして`running`を返している間はその状態を受け入れ、これらの後続操作を保留する。ファイルの差分、HEADコミット、更新時刻、無応答、経過時間などから停滞を推測することや、補助観測や催促の結果だけを根拠として後続操作を開始しない。

元担当を持たないレーン担当エージェント、独立したレビュー担当、CI修正担当の起動は、`references/runtime-routing.md`「工程別モデル設定」に従う。

## agents_serverの二層待機

外側セルの`cell_id`または内側コマンドの`session_id`のいずれかが未回収である場合は、`share/rules-common.codex.md`「Codex固有の入出力」に定める手段を用いて同一の実行結果を回収する。
外側セルや`exec_command`におけるyieldは同一実行の継続区間として扱い、代替起動、中断、巻き取りは「後続操作の共通先行条件」を満たした場合に限り実行する。
`functions.exec`の内部で`agents_server`を起動し、`PostToolUse`フックがMCPの応答を直接観測できない環境では、起動応答に含まれる`root_session_id`を保持し、`atk agents wait --root-session-id <値>`へ渡す。CLIは状態ディレクトリの実在性と、確認済みのルートセッションとの一致を検証する。

`functions.exec`から`atk agents wait`を起動する場合も、`share/rules-common.codex.md`「Codex固有の入出力」に定めるコマンド回収契約に従い、外側セルの`cell_id`を`functions.wait`へ渡す。
待機CLIへタスク固有timeoutを渡さず、CLI自身が待機上限に達したときに対象が未終了なら、その結果を確かめてから新しい待機コマンドを実行する。
逐次待機やロック競合によって遅れて開始された後発待機の回収手順は、`references/waiting-and-monitoring.md`「`atk agents wait`の応答の扱い」に従う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/share/rules-common.codex.md：Codex固有の入出力：2026年10月19日」に記載されている。

委譲先の成果物側のみを補助観測する場合は、作業ツリーの状態を`git -C <絶対パス> status --porcelain=v1`と`git -C <絶対パス> rev-parse --short HEAD`で観測する。成果物ファイルは`wc -l -- <絶対パス>`および`stat -L -c %Y -- <絶対パス>`（macOSでは`stat -L -f %m -- <絶対パス>`）で観測する。セッション自体の稼働確認には、起動手段が提供する状態確認機能を使用する。
