# セッション状態ファイルとフラグ：Function hooksの終了要求

各状態の共通の設計、生存期間、排他、継承は`docs/development/internals-session-state-common.md`にある。
定期再確認の通常回収の判定は`docs/development/internals-session-state-function-hooks-recheck.md`にある。
利用上限による中断の再開予約は`docs/development/internals-session-state-function-hooks-limits.md`にある。
待機時圧縮の判定記録は`docs/development/internals-session-state-function-hooks-compaction.md`にある。

## Function hooksの終了要求

Claude Codeが会話イベントのハンドラーを登録するために読み込むプラグインモジュールを、Function hooks module（以下「mod」）と呼ぶ。
このmodと、Python側のCLIおよびStop hookは、同じセッションIDの読み込み目印（マーカー）と終了要求を共有する。
配置場所は`.claude/agent-toolkit-function-hooks/`ディレクトリとする。
親ディレクトリは絶対パス指定の`CLAUDE_CONFIG_DIR`、未設定時は`HOME`、さらに未設定時は`USERPROFILE`とし、Python側の一時ディレクトリ解決ロジックは使用しない。
このディレクトリ内に`marker-{session_id}.txt`と`request-{session_id}.txt`を配置する。
セッションIDとして受理するのは英数字、アンダースコア、ハイフンのみとする。

`session.start`の発火時に読み込み目印へ`ready`を書き込み、終了要求ファイルを`consumed`に初期化する。
Python側は、目印が`ready`であり、かつファイルの更新時刻が現在のClaude Codeプロセス起動時刻より後である場合に限り、終了要求ファイルへ`requested`を書き込んで`exit_requested`を返す。
`turn.complete`はメインエージェントのターンでのみこの値を読み取り、値を`consumed`へ書き換えて`/exit`をキューへ投入する。
目印がない場合やCodexでは、対話CLIプロセスを従来どおり終了させる。
予約の消費は次のCLIの実起動が担当し、終了コマンドは予約を保持する。

`/exit`はセッション限りの未完了タスクが残ると確認画面を表示するため、`turn.complete`は`/exit`を投入する直前に、`durable`が真ではないcronタスクを削除する。
タスク一覧の取得と削除は`$.tool.check`が`allow`の場合だけ行い、削除に失敗しても`/exit`は投入する。
終了要求は、セッションが配下の作業と結果を回収してから送る。

process-loopの統括と`atk run-skill`が起動したセッションでは、定期再確認の通常回収と同じ範囲のtaskを、メインエージェントが最終ターンを閉じる前にも削除する。
手順は`agent-toolkit/skills/atk-mode/playbooks/host-claude-code.md`「ターンの終え方」、経緯は`docs/development/design-hooks-periodic-recheck.md`にある。

Stop hookによる無進捗停止ガード（`busy_loop_guard.py`）からの終了要求は、Stopの全判定が完了した後に共通ハンドラー（`stop.py`）から発行される。
終了要求が`requested`か特定PIDへの終了要求として成立した場合、そのStop hookの応答は停止の通知だけになる。
このため`autonomous_exit_invoked`が偽のままでも、無進捗停止の終了要求は`turn.complete`で消費される。
`autonomous_exit_invoked`が記録するのは`atk agents-exit-session`の実行履歴だけで、Stop内部で発行した終了要求はこのフラグと独立に終了要求ファイルの状態で管理する。

通常のSessionEndでは、現行セッションの読み込み目印、終了要求、定期再確認の判定結果、および利用上限の記録ファイルを保持したまま、保持期限を過ぎた他セッションの古いファイルを回収する。
定期再確認の判定は`docs/development/internals-session-state-function-hooks-recheck.md`に従う。
終了理由が`clear`の場合は現行セッションの4ファイルも削除する。
保持期限の管理とファイル回収処理は`agents_exit_session.sweep_function_hook_files`が担当する。
各ファイルに保存する文字列は、用途ごとに定めた固定値に限る。

## 会話内操作の要求と再開

MCP・設定hookとmodは、`ui-request-{session_id}-{要求ID}.txt`と`ui-response-{session_id}-{要求ID}.txt`で期限付きの要求と応答を交換する。
これらは`ui-requests/{セッションIDのASCII和を64で割った余りの2桁hex}/`へ置き、終了目印など過去会話の状態をポーリングの列挙対象から分離する。
分割先は固定64ディレクトリに限り、要求・応答ファイルには既存の保持期限による回収も適用する。
Python側が要求をatomicに保存し、modが同じ要求IDの描画・予約・観測結果を書く。
同期要求は呼出元がfinallyで両ファイルを回収し、非同期観測は応答後、未処理要求は期限後に`_common/function_hook_requests.py`が回収する。
許可判定用の対話APIは`$.session.surfaces()`が返す対話画面の種別・実行中の版・実効autoModeを取得し、承認要求と実入力の一致を確認する側へ結果を渡す。

`restart-{session_id}.txt`はmodが所有する1回限りの再開印である。
背景waitの停止後、またはprocess-loopの再起動時に、`phase: pending`と停止したwaitのID・commandをJSONで保存する。
SessionStartが自動続行の送信前に`phase: resumed`へ変更する。
通常終了では保持し、clearと期限切れ回収は既存の`sweep_function_hook_files`へ統合する。
modの会話状態には描画本文・処理済みID・背景待機・反映予約を置き、稼働中の会話の再読込を越える寿命をホストへ委ねる。
会話状態と`$.ui.place`で置いた行は、会話の終了・`/clear`・`/resume`・`/branch`で初期化される。
切替後にも読み返す本文は、transcriptに残るツール入力から描く。

Code Modeの通常awaitでは、内側Bashの`tool_use_id`を圧縮の待機識別に使う。
設定PreToolUseが`repl_`付きBashの開始を、PostToolUse・PostToolUseFailureが終了を同じ要求形式でmodへ渡す。
この待機は`replExpiresAt`を持ち、TaskStopとStopの`background_tasks`一覧による回収から除く。
外側REPLのtask IDとの対応は作らない。
期限はBashの`timeout`（省略時は`BASH_DEFAULT_TIMEOUT_MS`、未設定時120000ms）に5秒を加える。
modは期限の単発タイマーを置き、再読込後も再設定するため、Postが届かなくても期限後は圧縮候補から外れる。
