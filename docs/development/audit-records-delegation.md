# 監査記録: 委譲の起動と文脈

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/delegation/references/base-contract.md：基本委譲契約：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>は通常のサブエージェントを独立文脈、forkを親の会話履歴の継承として区別する。
Codex CLI 0.157.1の`spawn_agent`スキーマも`fork_turns`で渡す履歴を選べる。
再検証は各ホストの通常起動と全履歴forkの入力契約を比較する。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：2026年8月

Claude Code 2.1.251で検証した。
委譲の調整を担うサブエージェントが`SendMessage`で子へ継続指示を送って待機表明で終えたとき、その子の完了通知は送信元のtranscriptへ現れなかった。
同じ調整役が`Agent`ツールで起動した子の完了通知は現れた。
再検証は調整役のtranscriptに現れる`task-notification`の件数を起動手段別に数える。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：同一セッション内の再開：2026年8月

Claude Code 2.1.241で、同一セッション内の起動元が保持した機械可読識別子を再開対象へ用いた構成では、再開後の完了報告を起動元が受け取れることを実際に動かして確かめた。
再確認では`claude --version`で前提版数を記録し、完了通知に含まれる識別子と`SendMessage`の終了状態を比べる。
Claude Code 2.1.241で検証した設定は、対象キーを含まない一時JSONを`--settings`へ指定した。
`--setting-sources project,local`でユーザー設定を読み込まず、プロセス側で`env -u CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`を前置した。
初期化イベントで`SendMessage`と`ListAgents`の公開を確認し、両toolの実呼び出しが成功した。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：実行時能力と通信scope：2026年9月4日

2026年9月4日、agent-toolkit 2.94.0で通常起動と軽量起動の設定読込先およびStop系フックの動作を検証した。
再検証は`agents_server`の通常起動と軽量起動を比較する。

## agent-toolkit/skills/delegation/references/claude-code-runtime.md：起動パラメーター：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>で、forkの背景実行、完了通知、forkからの再委譲制限とfork modeを設定しない場合の値を確認した。
同版の対話セッションで`Agent`を`subagent_type: "fork"`として起動した観測では、`SubagentStart`が委譲先規範を追加した。
再検証は対話と`-p`のそれぞれでfork modeを確認し、forkの起動結果、規範の配送および完了通知を比べる。

## agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月21日

2026年9月21日、Codex CLI 0.155.1で確認した。
`spawn_agent`は`fork_turns`で会話履歴の引継ぎ範囲を選ぶ。
起動した委譲先は対象worktreeの`AGENTS.md`と`SubagentStart` hookの追加本文を別入力として受け取る。
`send_message`は稼働中の主体への追加配送、`followup_task`は同じ主体の継続、`wait_agent`は終了待機、`interrupt_agent`は中断を担う。
agent-toolkitのCodex hookを実起動したテストでは共通の`rules-subagent.md`が追加され、Claude Code固有の`rules-subagent.claude-code.md`は追加されない。
agents_serverの通常委譲も共通規範を持つ一方、軽量な探索・書込・shell promptは持たない。
再検証では`codex --version`で対象版を記録する。
`rules_context_codex.main`へ`SubagentStart`を入力するテストと、`_agents_server/launch_prompts.py`の通常・軽量promptのテストを実行する。
生成したCodex hook manifestの`SubagentStart`起動コマンドも実行し、共通規範、ホスト固有規範および軽量委譲の境界を確認する。

## agent-toolkit/skills/delegation/references/codex-runtime.md：ツール名の読み替え：2026年9月27日

2026-09-27、Codex CLI 0.157.1の`spawn_agent`公開スキーマで`fork_turns`の`none`、`all`、正の整数文字列と、全履歴forkのモデル・reasoning effort継承を確認した。
再検証は現行ホストのツールスキーマを取得し、`fork_turns`と上書き引数の相互制約を確かめる。

## agent-toolkit/skills/delegation/references/routing.md：会話を引き継ぐ委譲：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>「Fork the current conversation」で、forkが親の会話履歴、モデルおよびツールを引き継ぎ、背景の再説明を減らす場面と同じ起点から複数案を試す場面に適することを確認した。
Codex CLI 0.157.1の`spawn_agent`公開スキーマでは`fork_turns="all"`が全履歴を渡し、モデルとeffortの上書きを受け付けない。
再検証は両ホストの公開スキーマと公式資料を読み、通常起動と全履歴forkの入力・モデルの差を比較する。

## agent-toolkit/skills/delegation/references/runtime-routing.md：modelとreasoning effort：2026年8月

本項は軽量モデルへreasoning effort `max`を割り当てた大きな作業で自動コンパクションが10回発生し、所要時間が大幅に伸びた観測に基づく（2026年8月、ユーザーからの報告）。
再検証は同じ組合せで同規模の作業を1件実行し、自動コンパクションの発生回数を観測することによる。

## agent-toolkit/skills/delegation/references/runtime-routing.md：実行手段：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>で`Agent`の`fork`型を確認した。
Codex CLI 0.157.1の`spawn_agent`公開スキーマは`fork_turns`省略または`"all"`の全履歴forkを示す。
再検証は両ホストの起動スキーマを取得し、親のモデルとeffortの継承条件を確かめる。

## agent-toolkit/skills/delegation/references/sub-agents.md：コンテキスト境界：2026年9月27日

2026-09-27、Claude Code 2.1.283の公式資料<https://code.claude.com/docs/en/sub-agents>とCodex CLI 0.157.1の`spawn_agent`公開スキーマで、通常起動とforkの履歴入力が異なることを確認した。
再検証は同じ起動文で通常起動と全履歴forkを行い、委譲先に渡る会話履歴の有無を比較する。
