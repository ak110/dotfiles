# セッション状態ファイルとフラグ：セッションの活動記録

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |

## セッションの活動記録

`_common/session_activity.py`は、OSアカウントの状態ディレクトリ配下にある`session-activity/{session_id}.json`へ、会話ごとの最新状態を保存する。
状態ディレクトリの取得元は`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir()`である。
`hook.py`は既存イベントの処理で活動記録を更新し、`submission_activity`はprocess-loopの開始前判定へ、対象リポジトリへ進行中のWI投入作業の活動状況を渡す。

- 保存する項目は、バージョン番号、会話ID、実行系、PIDと起動時刻、会話内の各エージェントの活動状態、親会話ID、セッション記録のパス、バックグラウンドタスクの有無、および更新時刻である
- SessionStart、UserPromptSubmit、各種ツールイベント、SubagentStartの発生時に活動を記録し、StopやSubagentStopでは実際のhookレスポンスが続行を求めるかを反映する
- 応答待ち（アイドル）状態とみなして活動から除外する条件には、セッション記録上のターン終了またはホスト側で登録されたidle状態であり、かつ稼働中の子エージェントが存在しないことを用いる
- SessionEndの発火時に活動JSONを削除する。
  記録されたPIDと起動時刻が生存プロセスと一致しなければ、取得側で失効として扱う
- 更新および削除処理は固定の`activity.lock`による排他制御下で実行し、会話ごとの過去履歴や終了済みの子エージェントの項目は保持せずに削除する
- 読み取れない活動記録は安全側に倒して活動候補として返す。
  関連する会話を特定できない生存プロセスは、どの作業にも属さないものとして集計から除く
- WI投入作業の属性情報は、`session-activity/submissions/{session_id}.json`へ会話ごとに保存する。
  保存項目は、バージョン番号、会話ID、作業種別（`add-awi-by-user`）、正規化された投入先URLの一覧、作業開始時刻（`started_at`。最初の`begin`で付け、投入先の置換では保つ）、更新時刻である。
  このファイルは`atk wi submission begin`が作成・更新し、`atk wi submission end`が照合の成立後か`--abort`で削除する。
  正常な`end`は報告用要約を`session-activity/submission-reports/{session_id}.json`へ保存し、Stop hookが最終応答との照合後に削除する。
  hookのSessionEndでは削除せず、セッション再開時にその属性を再び有効化できるようにする
- 開始前判定では、投入先がprocess-loopの対象リポジトリ群と重複している属性を持つ会話、およびその子孫の活動のみを待機理由とし、属性を読み取れない会話や属性を持たない会話は除外する
- 対象外とする会話とその子孫の判定には`_common/session_parents.py`による親子関係の解決結果を用い、process-loopの共通環境変数などの識別印だけで別ループの会話まで誤って除外しないようにする
