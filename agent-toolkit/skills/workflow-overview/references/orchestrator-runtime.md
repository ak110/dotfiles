# 統括セッションの起動・終了・再開と制御

統括を前提に案・回答・確認の選択肢を出す際は、起動形、turn終了とプロセス終了、再開の契機を分けて判断する。
`--resume`による再開と、稼働中のturnへの入力やモデル・effortの変更は、それぞれの公開操作と適用条件で確かめる。

以下はagent-toolkitの起動・監督コードから確認できる構成の要約である。
ホストCLI内部の目標評価や入力待ちについては、各engineの実機での再観測が別途必要となる。
コマンド欄は起動形の比較のための抜粋であり、認証・権限・出力などの引数を省いている。
実行手順には各公開コマンドの`--help`を用いる。
実装パスはagent-toolkitのplugin rootを基点とする。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 起動形の比較

| 起動形・engine | 起動と最初の入力 | turn終了とプロセスの扱い | 再開・外部制御 | 実装の所在 |
| --- | --- | --- | --- | --- |
| process-loop・claude | 対話型の`claude --model … --effort … <入力>`。初回入力は`/goal <目的文>` | 目標評価による継続とCLIの終了を区別する。監督側はturn終了だけでは戻らず、CLIの終了を待つ | 利用上限時は同じ会話を`--resume`して続行。通常の再開はprocess-loopの再開指定を使う。外部操作は次節 | `agent_toolkit/_atk/wi/process_loop_session.py`の`build_process_loop_prompt`、`_build_session_argv`、`_run_process_session`、`_run_cli_session` |
| process-loop・codex | 対話型の`codex --model … -c model_reasoning_effort=… <目的文>`。`/goal`接頭辞を除いて渡す | turn終了後も対話CLIの終了が必要。監督側はCLIの終了を待つ | 上限を検出するとCLIを停止し、対象別結果と未完了を検収する。同じ会話の再開はprocess-loopの再開指定から`codex resume`へ接続 | `agent_toolkit/_atk/wi/process_loop_session.py`の`_build_session_argv`、`_run_cli_session`、`agent_toolkit/_atk/wi/process_loop.py`の`run_coordinator` |
| process-loop・agy | 対話型の`agy --model … --effort … --mode auto -i <目的文>`。`/goal`接頭辞を除いて渡す | 監督側は対話CLIの終了を待つ。CLI内部のturn終了条件はこのコードでは規定しない | 再開指定は`--conversation`、IDが無い再開は`--continue`へ接続。Claude用の上限再開ループをagyへ一般化しない | `agent_toolkit/_atk/wi/process_loop_session.py`の`_build_session_argv`、`_run_process_session`、`_run_cli_session` |
| run-skill・claude | 非対話の`claude -p --model … --effort …`。初回入力は`/goal <目的文>` | 目標評価の継続を経て最終応答でCLIが終了する構成。起動元は終了後に報告の保存も検収する | 利用上限解除後は同じ会話を`claude -p --resume`で自動再開。明示再開は`atk run-skill --resume <RUN_ID>` | `agent_toolkit/_atk/run_skill.py`の`_run_locked`、`dispatch`、`_wait_session` |
| run-skill・codex | 非対話の`codex exec --model … -c model_reasoning_effort=… --json <目的文>`。`/goal`を付けない | 最終応答でCLIが終了する構成。起動元は終了後に報告の保存も検収する | 利用上限解除後は同じthreadを`codex exec resume`で自動再開。明示再開はRUN_IDを指定する | `agent_toolkit/_atk/run_skill.py`の`_run_locked`、`dispatch`、`_wait_session` |
| run-skill・agy | 非対話の`agy -p <目的文> --model … --effort … --output-format stream-json`。`/goal`を付けない | 非対話CLIの終了を待ち、起動元が報告の保存を検収する。ホスト内部の目標評価は仮定しない | 明示再開は同じ会話の`--conversation`へ接続。利用上限の自動再開はCLI出力から上限を検出できた場合に限る | `agent_toolkit/_atk/run_skill.py`の`_run_locked`・`_SessionOutput.receive`・`_wait_session`、`agent_toolkit/_common/antigravity_cli.py`の`build_command` |
| agents_server通常委譲・claude | Claude Agent SDK。`model`・`effort`と委譲promptを渡す。起動側は`/goal`を付けない | SDKの結果・会話状態を管理し、turnの結果を公開する。対話端末の入力待ちとは別の管理 | `send_message`で実行中turnへ配送し、終了済みなら同じ会話を再開。相対操作で変更したeffortは次turnへ反映し、本人の変更後は自動再開する。`kill`はturnへの中断要求 | `agent_toolkit/_agents_server/claude_options.py`、同ディレクトリの`claude.py`・`claude_channels.py`・`claude_turns.py`・`effort_control.py` |
| agents_server通常委譲・codex | App Serverの`turn/start`へprompt・`model`・`effort`を渡す。`/goal`を付けない | turnの終了イベントを管理し、結果を公開する。CLIの`exec`起動ではない | `send_message`と`kill`によるturn制御。次の`turn/start`は相対操作で更新したeffortを使い、本人の変更後は同じthreadを自動再開する | `agent_toolkit/_agents_server/codex_turns.py`、同ディレクトリの`codex.py`・`manager_resume.py`・`effort_control.py` |
| agents_server通常委譲・agy | `agy -p <委譲prompt> --model … --effort …`。`/goal`を付けない | 非対話CLIの結果をturnの結果へ変換する | `send_message`は終了済みturnから同じ会話の`--conversation`で継続する。実行中turnへの配送は拒否する。次turnの引数へ変更したeffortを反映し、本人の変更後は自動再開する。`kill`は所有CLIを停止する | `agent_toolkit/_agents_server/antigravity.py`と同ディレクトリの`cli_turn.py`・`effort_control.py`、`agent_toolkit/_common/antigravity_cli.py` |
| ユーザーが直接操作する対話中のメイン・claude | ユーザーが起動引数と入力を選ぶ。統括用の`/goal`を自動設定する経路の対象外 | 対話の入力待ちと、`/goal`設定時の目標評価による継続を分ける。ホスト内部の条件は個別確認する | ユーザーの入力・ホストの再開操作を使う | 統括用ランチャー`agent_toolkit/_atk/wi/process_loop_session.py`と`agent_toolkit/_atk/run_skill.py`の対象外。終了要求の連携は`hooks/session_exit.ts` |
| ユーザーが直接操作する対話中のメイン・codex | ユーザーが起動引数と入力を選ぶ。統括用ランチャーは適用されない | turn終了後の入力待ちや終了操作は現在のホストの起動条件による | ユーザーの入力・ホストの再開操作を使う | 統括用ランチャー`agent_toolkit/_atk/wi/process_loop_session.py`と`agent_toolkit/_atk/run_skill.py`の対象外。終了要求の連携は`agent_toolkit/_atk/agents_exit_session.py` |
| ユーザーが直接操作する対話中のメイン・agy | ユーザーが起動引数と入力を選ぶ。統括用ランチャーは適用されない | turn終了後の入力待ちや終了操作は現在のホストの起動条件による。CLI内部の条件は未確認 | ユーザーの入力・ホストの再開操作を使う | 統括用ランチャー`agent_toolkit/_atk/wi/process_loop_session.py`と`agent_toolkit/_atk/run_skill.py`の対象外 |

agents_serverの配送結果と、受信側による本文の読取・処置の完了は別々に確認する。
長いツール実行などの途中へ即座に割り込めるという案は、該当engineの配送条件を別途確かめる。
配下担当の終了待ちによる自動再開は`agent_toolkit/_agents_server/resume_waits.py`が管理する。
process-loopの対話CLIを制御する公開操作は次節の表で確認する。

## 常駐・単発・明示再開の違い

- `atk wi process-loop`は登録集合に対して統括を1つ起動し、そのCLIの終了後に対象別結果・残件・writerの終了を検収して次の取得へ進む。
  `--once`は同じ起動・検収を1集合で終え、未完了などが残れば非0で終了する。
  手動の`agent-toolkit:process-wi all-repos`は`--once`へ接続し、起動元がCLIの終了を観測する。
  根拠は`agent_toolkit/_atk/wi/process_loop.py`の`run_coordinator`・`_run_loop`と、`../../process-wi/references/all-repos-starting.md`にある。
- process-loopの`--resume <session ID>`と`--auto-resume`は保持した所有・会話から再開する指定である。
  再開時も保持したモデルとeffortを使う。
  根拠は`agent_toolkit/_atk/wi/process_loop.py`の`run_coordinator`と`agent_toolkit/_atk/wi/process_loop_session.py`の`select_available_orchestrator`にある。
- run-skillの`--resume <RUN_ID>`は保存した入力・報告先・残時間・会話を再利用する。
  同じ対象のロックと旧実行主体の終了を確認し、再開対象を未完了の実行に限る。
  `review-session batch`も同じ非対話起動を使い、起動前後の対象確定・分析結果の検収はbatch側が行う。
  根拠は`agent_toolkit/_atk/run_skill.py`の`dispatch`・`_dispatch_once`・`_run_locked`と`agent_toolkit/_atk/run_skill_batch.py`にある。

## 外部制御とモデル・effort

| 対象 | 制御できる範囲 | 設定が決まる時点 |
| --- | --- | --- |
| process-loop | `abort`は現在の統括終了後に常駐を止める要求。`instruct`は次に起動する1セッションへの指示。`status`は中断要求と保持中の指示を表示する。これらは稼働中の統括へのメッセージ送信や即時停止ではない | 新規統括の起動前に候補設定を読み直して可用性を判定する。同じ会話の再開は登録済みのmodel・effortを使う |
| run-skill | 公開の再開指定はRUN_IDを受け取る。実行中の会話へ指示を送る引数や、再開時のmodel・effort上書き引数は持たない | 新規実行で候補を解決・選択する。明示再開と利用上限後の再開は元のmodel・effortを使う |
| agents_server通常委譲 | `send_message`・`kill`でturnを制御する。本人と直上の委譲元は`atk agents effort up\|down [session_id]`で1段ずつ変更できる | `start`で候補を選択する。modelは保持し、更新したeffortを同じ会話の次turnへ適用する。本人が変更した場合はturn終了後に1回自動再開する |
| ユーザーが直接操作する対話中のメイン | 各ホストの入力・設定操作を使う。統括用ランチャーによる変更可否の保証は無い | 起動引数・ホスト設定・会話内操作による。途中変更の可否は対象ホストの公開仕様で別途確認する |

process-loopの制御は`agent_toolkit/_atk/wi/process_loop_control.py`と同ディレクトリの`process_loop.py`にある。
run-skillの入力と再開は`agent_toolkit/_atk/run_skill.py`、委譲先の制御は`agent_toolkit/_agents_server/manager.py`と同ディレクトリの`effort_control.py`で確認する。
effort操作は全4engineの通常起動（`task`・`delegate`）に適用し、本人は対象IDを省略、直上の委譲元は子のIDを指定する。
軽量起動（`explore`・`shell`・`write`）、統括（`orchestrate`）、対話中のメインは対象外であり、実行中turnを中断して即時反映する操作は持たない。
`send_message`は`session_id`と自由文の`prompt`、または宣言済みの`instruction`と`extra_params`を受け付け、model・effortの変更引数は持たない。
候補解決の根拠は`agent_toolkit/_atk/orchestrator.py`の`resolve_specs`・`select_available_with_provider`と`agent_toolkit/_agents_server/manager_start.py`の`start`である。
設定の保存を稼働中turnへの反映と取り違えず、次の新規起動と同じ会話の再開を分けて提案する。

起動・制御の設計経緯は、開発リポジトリの`docs/development/design-process-loop.md`、`design-cli.md`、`design-agents-runtime.md`の索引にある。
配布環境でこれらの記録が無い場合も、上記のプラグイン内実装と公開ヘルプから構成を確認する。
