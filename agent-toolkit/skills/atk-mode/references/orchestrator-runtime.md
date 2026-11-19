# 統括セッションの起動・終了・再開と制御

process-loop・run-skillの統括の挙動（起動、turn終了、再開、外からの制御）を前提に、案・回答・確認の選択肢を出す前に読む。
起動形、turn終了とプロセスの終了、再開の契機を分けて判断し、会話の再開と、稼働中のturnへの入力やモデル・effortの変更は、それぞれの公開操作と適用条件で確かめる。
下表は起動・監督コードから確かめた構成の要約で、コマンドは比較のための抜粋である（認証・権限・出力の引数を省く）。
実行の手順は各公開コマンドの`--help`を使う。
実装の所在と設計の経緯は、開発リポジトリの`docs/development/design-process-loop-control.md`、`design-cli-run-skill.md`、`design-agents-runtime-state.md`にある。
配布環境でこれらが無い場合は、plugin rootの`agent_toolkit/_atk/wi/process_loop*.py`、`agent_toolkit/_atk/run_skill.py`、`agent_toolkit/_agents_server/`と公開ヘルプから確かめる。

## 起動形の比較

| 起動形・engine | 起動と最初の入力 | turn終了とプロセスの扱い | 再開・外部制御 |
| --- | --- | --- | --- |
| process-loop・claude | 対話型の`claude --model … --effort … <入力>`を常駐の端末の前景で起動する。初回は目的文、再開は元の目的の続行入力 | 終了工程`atk agents-exit-session`の実行記録が無い終了はStop hookが遮断して続行させる。workerはCLIの終了を待つ | 利用上限時は同じ会話を`--resume`して続行。通常の再開はprocess-loopの再開指定を使う |
| process-loop・codex | 対話型の`codex --model … -c model_reasoning_effort=… <目的文>`を常駐の端末の前景で起動する | 同上。turn終了後も対話CLIの終了が要る | 上限を検出するとCLIを止め、対象別の結果と未完了を検収する。同じ会話の再開は予約から`codex resume`へ接続 |
| process-loop・agy | 対話型の`agy --model … --effort … --mode auto -i <目的文>`を常駐の端末の前景で起動する | 同上（変換層経由のStop hook）。終了工程はLinuxの対話agyへ終了要求を送る | 再開指定は`--conversation`。IDを省略した会話選択は非対応 |
| run-skill・claude | 非対話の`claude -p --model … --effort …`。初回入力は目的文 | 報告用UWIの保存を確かめられない終了はStop hookが遮断する。バックグラウンドタスクの完了待ちのturn終了は許し、完了通知で再開する。最終応答でCLIが終わり、起動元が報告の保存を検収する | 利用上限の解除後は`claude -p --resume`で自動再開。明示再開は`atk run-skill --resume <RUN_ID>` |
| run-skill・codex | 非対話の`codex exec --model … -c model_reasoning_effort=… --json <目的文>` | 同上 | 利用上限の解除後は`codex exec resume`で自動再開。明示再開はRUN_ID |
| run-skill・agy | 非対話の`agy -p <目的文> --model … --effort … --output-format stream-json` | 同上（変換層経由） | 明示再開は`--conversation`。利用上限の自動再開はCLI出力から上限を検出できた場合だけ |
| agents_server通常委譲 | claudeはClaude Agent SDK、codexはApp Serverの`turn/start`、agyは`agy -p`へ、prompt・`model`・`effort`を渡す | turnの結果を管理して公開する。対話端末の入力待ちとは別の管理 | `send_message`は実行中turnへ配送し、終了済みなら同じ会話を再開する（agyは実行中turnへの配送を拒否する）。`kill`はturnへの中断要求 |
| ユーザーが直接操作する対話中のメイン | ユーザーが起動引数と入力を選ぶ。統括用ランチャーは適用されない | turn終了後は入力を待つ。統括とrun-skill向けのStop hookの続行判定は働かない | ユーザーの入力とホストの再開操作。Claude Codeの`/restart`・`/update`は同じPID・会話IDで再起動し、modが自身の背景waitを停止した場合だけ自動で続行する |

process-loopの統括とrun-skillのセッションは、目的文（`atk-auto`要素）を最初の入力として受け取り、完了までの続行はStop hookの機械判定が担う。
判定は環境印で識別した最上位だけに働き、委譲先と人が操作する対話セッションには働かない。
判定が見るのは終了工程の実行記録と報告用UWIの保存であり、作業内容の未達は常駐と起動元の検収が検出する。
遮断が連続上限に達すると、process-loopの統括は終了要求で終わって常駐の検収へ戻り、run-skillは起動元が報告用UWI未保存の失敗として報告する。
agents_serverの配送結果と、受信側が本文を読んで処置したことは別々に確かめる。
長いツール実行の途中へ即座に割り込めるという案は、該当engineの配送条件を確かめてから出す。

## 対象の分担と単回実行

常駐は今回の全対象を1つの統括会話へ渡し、対象ごとの準備・所有・終了検収を管理する。
同時に起動する統括会話は1つで、統括は選定・レビュー・公開・CI・完了報告・報告用UWI・結果の保存まで担当する。
会話の再開は`resume [ID|auto]`で予約した場合に限り、予約の無い起動は新しい会話で始める。
今回の対象が1リポジトリならレーン数の上限によらず統括が自ら処理し、複数なら負荷が最大のリポジトリを統括が直接担当する。
具体的な選定と継続は`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「開始と振り分け」に従う。
登録と再開の条件は`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`に従う。
計画・検証・レビュー・公開は対象worktreeごとに分け、process-wiの実行全体の完了報告は統括が行う。

非常駐の全体処理は`agent-toolkit:atk-mode`の作業`process-wi all-repos`を手動で起動し、同作業は`atk wi process-loop --once`で常駐と同じ取得・所有・準備・検収・回収を1集合に対して行う。
対象が0件なら待たずに終わり、未完了・所有待ち・準備失敗が残れば非0で終わる。
次回の再開は`atk wi process-loop resume auto`で予約し、停止中は予約の後に`--once`を起動する。
`resume ID`は指定会話、値を省いた`resume`はCLIの会話選択画面を予約し、`resume-cancel`は未消費の予約を取り消し、予約状態は`status`で確かめる。
対象リポジトリの単発処理は`agent-toolkit:atk-mode`の作業`process-wi`か`agent-toolkit:atk-mode`の作業`process-wi single-lane`を使う。

## 常駐・単発・明示再開の違い

- `atk wi process-loop`は統括会話のCLIの終了後に、対象ごとの結果・残件・writerの終了を検収してから次の統括会話を始める。
- process-loopの`resume [ID|auto]`は次の実起動への予約である。
  予約は起動の成功で1回消費し、起動前の失敗とabortでは保持する。
  再開は未完了の元会話・候補・計画・`lane_session_id`と、保持したモデル・effortを再利用する。
- run-skillの`--resume <RUN_ID>`は保存した入力・報告先・残時間・会話を再利用し、同じ対象のロックと旧実行主体の終了を確かめ、未完了の実行だけを再開する。
  `review-session batch`も同じ非対話起動を使い、対象の確定と分析結果の検収はbatch側が行う。

## 継続中の公開と更新

対象のメインは30分点検で、未公開の統合済み変更、前回の公開・リリース、稼働版とセッションの開始時刻を既存の記録から確かめる。
公開できる変更があれば、プロジェクトの通常の公開・リリースの時機、未公開の統合済みWIが10件に達した時点、最初の未公開の統合commitから2時間30分のうち早い時機に公開を始める（10件は1レーン分の上限、2時間30分は点検の間隔を含め公開開始を3時間以内にする値）。
公開を始める時に、push、CI検収、リリースPR、マージ、配布の順で、実行するコマンドの時間上限と呼出し回数から通常時の所要上限を合成し、公開開始までの3時間に加えた値を通常時のリリース間隔の上限として既存の検証記録へ残す。
上限の無い段階が残る場合は、有限の実行手段を選ぶか、保証範囲を未確定として報告する。
未検証の変更を期限のために公開せず、外部待ちで期限を守れない場合は、期限・阻害・次の確認時機を未達事項として保存する。
新着の受け入れや同じ会話での継続を理由に、既存のリリース頻度を下げない。

規範・配布版の更新は、同じ会話で更新を取り込めるホストの公開手段を優先し、使えない場合は`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「更新のための本人再開」で同じ会話の未完了作業を保持する。
稼働開始から20時間の点検で新しい作業の開始を保留し、保存、公開、writerの終了観測、更新、同じ会話の再開、稼働版の確認へ進む。
各段階の上限（`update-dotfiles`の60分、起動時の実ID確定の120秒など）を点検の最大30分と合成し、24時間までの残りに収まるかを確かめ、超える場合はその差だけ早く保守へ入る。
実際の停止・再開を観測できない場合は、最長稼働時間や版の取り込みを保証済みと扱わず、阻害と未完了の工程を上流へ返す。
同じ会話への規範・プラグインの反映だけではホストの実行バイナリは更新されないので、ホストの版を確かめて本人再開の要否を判定する。

## 外部制御とモデル・effort

| 対象 | 制御できる範囲 | 設定が決まる時点 |
| --- | --- | --- |
| process-loop | `abort`は新規起動を止め、現在の全実行の終了後に常駐を止める要求。`instruct`は次に実起動する1会話への指示。`resume`・`resume-cancel`は次回再開の予約と取消、`status`は中断・指示・予約の表示。どれも稼働中の統括へのメッセージ送信や即時停止ではない | 新規統括の起動前に候補設定を読み直して可用性を判定する。同じ会話の再開は登録済みのmodel・effortを使う |
| run-skill | 再開指定はRUN_IDを受け取る。実行中の会話へ指示を送る引数と、再開時のmodel・effortの上書き引数は持たない | 新規実行で候補を解決する。明示再開と利用上限後の再開は元のmodel・effortを使う |
| agents_server通常委譲 | `send_message`・`kill`でturnを制御する。本人と直上の委譲元は`atk agents effort up\|down [session_id]`で1段ずつ変更できる（通常起動の`task`・`delegate`だけ。軽量起動、統括、対話中のメインは対象外） | `start`で候補を選ぶ。modelは保持し、更新したeffortを同じ会話の次turnへ適用する。本人が変更した場合はturn終了後に1回自動で再開する |
| ユーザーが直接操作する対話中のメイン | 各ホストの入力・設定操作を使う。統括用ランチャーによる変更可否の保証は無い | 起動引数・ホスト設定・会話内の操作による |

`send_message`は`session_id`と自由文の`prompt`、または宣言済みの`instruction`と`extra_params`を受け付け、model・effortの変更引数は持たない。
設定の保存を稼働中turnへの反映と取り違えず、次の新規起動と同じ会話の再開を分けて提案する。
