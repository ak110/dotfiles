# セッション状態ファイルとフラグ：Function hooksの終了要求：定期再確認の通常回収の判定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Function hooks用ファイルの配置とSessionEnd・clear・期限切れ回収を確認する前 | `session-state-and-flags-function-hooks.md` |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## 定期再確認の通常回収の判定

`recheck-{session_id}.txt`は`session-state-and-flags-function-hooks.md`で定めるFunction hooks用ディレクトリに配置し、Python側のStop入力を同一セッション内のmodへ1回限りで受け渡すために使用する。
セッション限りの定期再確認タスクの作成、維持、回収はmodが担当し、待機対象タスクの一覧は既存の非同期判定ロジックおよび`CronList`から取得する。

- 書き込み元: `agent_toolkit/_hooks/stop.py`が各Stopの開始時に`unknown`を書き込む。
  終了判定の集約後、既存の`background_tasks.is_pending_async_work`へtranscript、会話ID、バックグラウンドタスク一覧、および現在のセッション状態を渡す。
  判定結果に応じて`pending`か`complete`へ更新する。
  読み取れないtranscript、データの欠落や破損がある場合、あるいはセッション継続要求を伴うStopでは`unknown`を維持する。
  この処理はClaude Code会話のメインエージェントにのみ適用し、`agent_id`を持つサブエージェントやCodexは対象外とする
- 消費元: `hooks/session_exit.ts`にある既存の`turn.complete`が、判定を利用する直前に`consumed`へ書き換える。
  `complete`の値のみを通常回収の根拠として扱い、`$.tool.list()`によって`CronList`の公開が確認できた会話に適用する。
  非公開の会話では回収と通知を省き、正常に復帰する。
  適用対象の環境では、共通のプロンプトと一致する非durableな所有タスクを一覧から再度特定して削除し、所有タスクの削除が拒否または失敗した場合は対象タスクIDとともに通知する。
  追加のクリーンアップ義務が付随するタスクは、セッション終了時の既存の回収処理へ引き渡す。
  欠落、読み取り不能、不明な値の場合はタスク回収を許可しない
- 初期化と生存期間: `hooks/register.ts`が`session.start`およびメインエージェントの`turn.start`で`consumed`に初期化し、中断された前ターンの判定結果を持ち越さないようにする。
  ファイルの内容は各Stopの実行ごとに上書きされ、回収の拒否や失敗が発生した後は、次の有効なStopにおいて再評価する。
  ファイル自体の寿命は、`session-state-and-flags-function-hooks.md`のSessionEnd・`clear`・期限切れ回収の規定に従う
