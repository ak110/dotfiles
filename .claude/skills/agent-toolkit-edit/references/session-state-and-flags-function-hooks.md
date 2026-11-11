# セッション状態ファイルとフラグ：Function hooksの終了要求

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

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

`/exit`はセッション限りの未完了タスクが残ると確認画面を表示するため、`turn.complete`は`/exit`を投入する直前に、`durable`が真ではないcronタスクを削除する。
タスク一覧の取得と削除は`$.tool.check`が`allow`の場合だけ行い、削除に失敗しても`/exit`は投入する。
process-loopによって起動された会話の最上位Stopでは、終了要求が`requested`であり、かつStop入力の`background_tasks`に有効な通常タスク（非`teammate`タスク）が残っているかどうかを判定する。
この条件に該当する場合、`agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`は終了要求を`consumed`に戻して終了をブロックする。
Stop hookは同じターンの`turn.complete`より先に動くため、取り下げた終了要求で`/exit`が実行されることはない。

`/goal`の評価もStopの時点で行われ、未達ならターンを継続させるため、`turn.complete`の後始末が動くのは評価の後になる。
Stop入力の`session_crons`・`background_tasks`・`last_assistant_message`に未完了と読める状態を残す後始末を`turn.complete`へ置く場合は、目標の設定下でその状態を評価より前に解消する主体と手順を別に定める。
定期再確認の通常回収は、目標の設定下ではメインエージェントが最終ターンを閉じる前に削除する（`agent-toolkit/share/rules/rules-main.claude-code.md`「ターンの終え方」）。

Stop hookによる無進捗停止ガード（`busy_loop_guard.py`）からの終了要求は、Stopの全判定が完了した後に共通ハンドラー（`stop.py`）から発行される。
終了要求が`requested`か特定PIDへの終了要求として成立した場合、そのStop hookの応答は停止の通知だけになる。
このため`autonomous_exit_invoked`が偽のままでも、無進捗停止の終了要求は`turn.complete`で消費される。
`autonomous_exit_invoked`が記録するのは`atk agents-exit-session`の実行履歴だけで、Stop内部で発行した終了要求はこのフラグと独立に終了要求ファイルの状態で管理する。

通常のSessionEndでは、現行セッションの読み込み目印、終了要求、定期再確認の判定結果、および利用上限の記録ファイルを保持したまま、保持期限を過ぎた他セッションの古いファイルを回収する。
定期再確認の判定は`session-state-and-flags-function-hooks-recheck.md`に従う。
終了理由が`clear`の場合は現行セッションの4ファイルも削除する。
保持期限の管理とファイル回収処理は`agents_exit_session.sweep_function_hook_files`が担当する。
各ファイルに保存する文字列は、用途ごとに定めた固定値に限る。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 定期再確認の通常回収の判定に関する判断・作業の前 | `session-state-and-flags-function-hooks-recheck.md` |
| 利用上限による中断の再開予約に関する判断・作業の前 | `session-state-and-flags-function-hooks-limits.md` |
| 待機時圧縮の判定記録に関する判断・作業の前 | `session-state-and-flags-function-hooks-compaction.md` |
