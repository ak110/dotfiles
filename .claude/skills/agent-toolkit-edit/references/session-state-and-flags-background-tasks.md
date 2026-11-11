# セッション状態ファイルとフラグ：バックグラウンドタスク系

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## バックグラウンドタスク系

- `background_task_ids`: PostToolUseが、自セッション内のツール呼び出しの応答から取得したタスクIDを重複なく記録する。
  この記録を「バックグラウンドタスクの所有記録」と呼ぶ。
  ここでいうバックグラウンドタスクとは、BashやAgentを背景で実行した非同期処理を指す。
  記録の契機は以下の4つであり、自身の呼び出しに対して識別子が返されたことが所有の根拠となる
  1. Bashで`run_in_background`を指定した際のレスポンス（成功・失敗を問わず）
  2. 実行時間の上限到達やユーザーの操作によってバックグラウンドへ移行したBashのレスポンスに含まれる構造化データ`backgroundTaskId`
  3. MCPツールの呼び出しがバックグラウンドへ移行した際にホストから送られる移行通知。
     行頭から始まる`MCP tool "<ツール名>" is still running after <秒数>s. It was moved to the background as task <ID>`という形式の通知のみを記録対象とし、フォアグラウンドの出力本文の途中に現れる同一の文言は除外する
  4. `Agent`や`Task`のバックグラウンド起動のレスポンス（`status`が`async_launched`）から返された`agentId`

  PreToolUse(TaskStop)は、停止対象のタスクが自セッションのバックグラウンドタスクであるかどうかの判定にこの記録を参照する。
  また、PreToolUse(Bash)も未完了タスクの出力ファイルを読み取ろうとした際の警告判定の前にこの記録を参照し、タスクが1件以上記録されている場合に限りtranscriptを読み取る。
  なお、出力先ファイルのパスは状態ファイルには記録せず、`background_tasks.py`と同様に起動集合と完了集合から特定した未完了のバックグラウンドBashについて、起動時の`tool_result`本文から取得する。
  この記録はセッション終了まで保持する。
- `queued_notification_notified_ids`: Stopの`queued_notification_advisor.py`が、最上位transcriptのキューに残っている未配信の完了通知に関して、既に案内した通知の識別子を重複なく記録する。
  識別子には`<task-id>`を用い、存在しない場合は`<tool-use-id>`、どちらも存在しない場合は通知本文そのものを使用する。
  AgentやTaskの起動記録に対応する通知については返却メッセージの確認を案内し、それ以外は出力ファイルの読み取りを案内する。
  他のStop判定によるブロックが存在する場合に限り、同一通知に対する警告を1回のみに制限するための判定にこのキーを参照する（繰り返し警告すると、ツールを呼び出さないままターンが継続してしまうループに陥るため）。
  他のブロックが存在しない場合は、案内済みであるかどうかにかかわらず現在未配信の通知を採用し、ホストによる配送を待って次のターンへ進む。
  警告回数と本キーは実際に警告する場合だけ更新する。
  セッション終了まで保持し、リセット処理の対象から除く
- `task_stop_blocked_at`: PreToolUse(TaskStop)が停止操作を遮断した時刻（POSIX秒）。
  同フックが再実行許可ウィンドウを判定する際に参照する。
  セッション終了まで保持し、リセットしない
- `stall_detection_completed_at_by_task`: `record_stall_detection.py`がプロセスの停滞検知を完了したタスクについて、完全なタスクIDをキー、検知完了時刻（POSIX秒）を値として記録する。
  PreToolUse(TaskStop)は、完了時刻から5分以内の対象タスクに限り停止可能と判定し、期限切れとなったエントリは除外する。
  停止処理が成功したPostToolUse(TaskStop)が該当エントリを消費し、停止に失敗した場合は同じ5分間の枠内で再試行できるようにエントリを保持する
