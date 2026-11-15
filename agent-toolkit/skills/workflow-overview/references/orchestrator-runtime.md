# 統括セッションの起動・終了・再開と制御

統括を前提に案・回答・確認の選択肢を出す際は、起動形、turn終了とプロセス終了、再開の契機を分けて判断する。
会話の再開と、稼働中のturnへの入力やモデル・effortの変更は、それぞれの公開操作と適用条件で確かめる。

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
| process-loop・claude | 通常は非対話の`claude -p --model … --effort … <入力>`。初回は`/goal <目的文>`、再開は元goalの続行入力 | 最終応答で終了するCLIを対象workerが観測する。会話選択画面を使う場合は対話CLIの終了が必要 | 利用上限時は同じ会話を`--resume`して続行。通常の再開はprocess-loopの再開指定を使う。外部操作は次節 | `agent_toolkit/_atk/wi/process_loop_session.py`の`build_process_loop_prompt`、`_build_session_argv`、`_run_process_session`、`_run_cli_session` |
| process-loop・codex | 通常は非対話の`codex exec --model … --json <目的文>`。`/goal`接頭辞を除いて渡す | 最終応答で終了するCLIを対象workerが観測する。会話選択画面は対話型の`codex resume`で開く | 上限を検出するとCLIを停止し、対象別結果と未完了を検収する。同じ会話の再開は予約から`codex exec resume`または対話型の`codex resume`へ接続 | `agent_toolkit/_atk/wi/process_loop_session.py`の`_build_session_argv`、`_run_cli_session`、`agent_toolkit/_atk/wi/process_loop.py`の`run_coordinator` |
| process-loop・agy | 通常は非対話の`agy -p <目的文> --model … --effort … --mode auto --output-format stream-json`。`/goal`接頭辞を除いて渡す | 最終応答で終了するCLIを対象workerが観測する | 再開指定は`--conversation`、IDを省略した会話選択は非対応のためIDかautoを案内。Claude用の上限再開ループをagyへ一般化しない | `agent_toolkit/_atk/wi/process_loop_session.py`の`_build_session_argv`、`_run_process_session`、`_run_cli_session` |
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

## 対象の分担と単回実行

常駐schedulerは最大2つの対象別workerを別プロセスで起動し、対象ごとの準備・所有・終了検収・次回開始を管理する。
各対象のメインは、別対象の終了を待たず、選定・レビュー・公開・CI・完了報告・報告用UWI・結果保存まで担当する。
複数対象の未完了作業を持つ旧会話の明示再開では、元の会話と集合を保持し、次の統括・委譲の分担を引き継ぐ。
統括は対象の規範・スキルを明示的に読み、今回の対象が1リポジトリならレーン数上限によらず自ら処理する。
対象が複数の場合も、今回進められる負荷が最大のリポジトリを統括が直接担当する。
全対象の上限が1以下なら統括が全件をまとめ、上限2以上の対象がある場合は、直接担当を除く軽い対象群を1担当へまとめ、重い残対象は個別に並行委譲する。
具体的な選定と通常の継続は`../../process-wi/references/all-repos-starting.md`「開始と振り分け」に従う。
直接処理でも上限が2以上なら通常のレーン処理へ、1以下ならsingle-laneへ接続し、起動したwriterの終了観測と常駐への対象別結果の保存を保つ。
計画・検証・レビュー・公開は対象worktreeごとに分離し、process-wiの実行全体の完了報告は統括が行う。
常時処理するリポジトリだけ`process_loop`を有効にし、投入だけを行うリポジトリも`edit=awi`として登録できる。
非常駐の全体処理は`agent-toolkit:process-wi all-repos`を手動起動する。
同スキルは`atk wi process-loop --once`で、常駐と共通の対象取得・所有・worktree準備・結果検収・回収を1集合に対して行う。
対象が0件なら待機せず終了し、未完了・所有待ち・準備失敗が残れば非0で終了する。
表示された記録を確認し、`atk wi process-loop resume auto`で次回の再開を予約する。
停止中は予約後に`atk wi process-loop --once`を起動する。
`resume ID`は指定会話、値を省略した`resume`はCLI本来の会話選択画面を予約し、`resume-cancel`は未消費予約を取り消す。
予約状態は`status`で確認する。
手動起動元はCLIの終了を観測して結果を返す。
完了報告とUWIの保存は各対象のメインが行い、旧集合の明示再開では統括が実行全体で1回行う。
対象リポジトリの単発処理は`agent-toolkit:process-wi`または`agent-toolkit:process-wi single-lane`を使う。
登録や再開の条件は`../../process-wi/references/all-repos-orchestration.md`に従う。

## 常駐・単発・明示再開の違い

- `atk wi process-loop`は対象別workerを保持し、その対象のCLI終了後に結果・残件・writerの終了を検収する。
  完了対象の次回開始は、別対象の実行終了を待たない。
  `--once`は同じ起動・検収を1集合で終え、未完了などが残れば非0で終了する。
  手動の`agent-toolkit:process-wi all-repos`は`--once`へ接続し、起動元がCLIの終了を観測する。
  根拠は`agent_toolkit/_atk/wi/process_loop.py`の`run_coordinator`・`_run_loop`と、`../../process-wi/references/all-repos-starting.md`にある。
- process-loopの`resume [ID|auto]`は次の実起動への予約である。
  値省略はホストCLIの会話選択画面であり、選択した実ID/cwdをSessionStartから受領して対象の所有を確定する。
  予約は実Popen成功で1回消費し、起動前失敗・abortでは保持する。
  未完了の元会話・候補・計画・lane_session_idを再利用する。
  再開時も保持したモデルとeffortを使う。
  根拠は`agent_toolkit/_atk/wi/process_loop.py`の`run_coordinator`と`agent_toolkit/_atk/wi/process_loop_session.py`の`select_available_orchestrator`にある。
- run-skillの`--resume <RUN_ID>`は保存した入力・報告先・残時間・会話を再利用する。
  同じ対象のロックと旧実行主体の終了を確認し、再開対象を未完了の実行に限る。
  `review-session batch`も同じ非対話起動を使い、起動前後の対象確定・分析結果の検収はbatch側が行う。
  根拠は`agent_toolkit/_atk/run_skill.py`の`dispatch`・`_dispatch_once`・`_run_locked`と`agent_toolkit/_atk/run_skill_batch.py`にある。

## 継続中の公開と更新

対象のメインは30分点検で、未公開の統合済み変更、前回の公開・リリース、稼働版とセッションの開始時刻を既存記録から確認する。
公開可能な変更があれば、プロジェクトの通常の公開・リリース時機、未公開の統合済みWIが10件に達した時点、最初の未公開の統合commitから2時間30分のうち早い時機に公開を始める。
10件は通常の1レーン分をまとめる量の上限、2時間30分は点検の間隔を含め公開開始を3時間以内にするための値である。
公開開始時に、push、CI検収、リリースPR、マージ、配布の順で、実行するコマンドの時間上限と呼出し回数から通常時の所要上限を合成する。
各上限は実装または実行時の指定へ対応付け、入れ子の最終照会と終了回収の時間も含める。
成功記録の所要は上限と区別し、版・引数・入力規模・CI構成・並列度・準備状態・呼出し回数が同等の実行の見積りに使う。
上限のない段階が残る場合は、既存の有限実行手段を選ぶか、保証範囲を未確定として報告する。
公開開始までの3時間にこの合計を加えた値を、通常時のリリース間隔の上限として既存の検証記録へ残す。
公開・CI・リリースの安全条件と外部失敗時の報告は維持し、未検証の変更を期限のために公開しない。
外部待ちで期限を守れない場合は、期限・阻害・次の確認時機を未達事項として保存する。
新着の受入れや同じ会話での継続を理由に、既存のリリース頻度を下げない。
未完了作業の独立したレーンを保持して公開した後は、その作業を同じ巡回で続ける。

規範・配布版の更新は、同じ会話で更新を取り込めるホストの公開された反映手段を優先する。
手段が使えない場合は`../../process-wi/references/all-repos-orchestration-resources.md`「更新のための本人再開」で同じ会話の未完了作業を保持する。
ホストの版の更新も同じ保守の区切りへまとめ、稼働開始から20時間の点検で新しい作業の開始を保留し、保存、必要な公開、writerの終了観測、更新、同じ会話の再開、稼働版の確認へ進む。
各段階の有限上限を点検の最大30分と合成し、24時間までの残りに収まるか確認する。
更新の実行上限は既存の`update-dotfiles`の60分、起動時の実ID確定は120秒であり、保存・公開・終了観測の上限も実行するコマンドと回数から別に求める。
4時間は予算であり、合成値が超える場合はその差だけ早く保守へ入る。
通常時の最長稼働時間と更新の取り込み遅延は、この開始時刻と合成値から検収する。
委譲先は保存成果と更新要求を直上へ返し、常駐・対象メイン・配下担当の所有と実行識別子を保って順に切り替える。
実際の停止・再開を観測できない場合は、最長稼働時間や版の取り込みを保証済みと扱わず、阻害と未完了工程を上流へ返す。
外部CIの停滞、応答しないwriter、更新の失敗は通常時の上限と分け、所有を保持して報告する。
同じ会話への規範・プラグイン反映だけではホストの実行バイナリは更新されないため、その場合もホストの版を確認して本人再開の要否を判定する。

## 外部制御とモデル・effort

| 対象 | 制御できる範囲 | 設定が決まる時点 |
| --- | --- | --- |
| process-loop | `abort`は新規起動を止め、現在の全実行の終了後に常駐を止める要求。`instruct`は次に実起動する1会話への指示。`resume`・`resume-cancel`は次回再開の予約・取消、`status`は中断・指示・予約を表示する。これらは稼働中の統括へのメッセージ送信や即時停止ではない | 新規統括の起動前に候補設定を読み直して可用性を判定する。同じ会話の再開は登録済みのmodel・effortを使う |
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
