# セッション状態ファイルとフラグ：agents_server連携系

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |

## agents_server連携系

- `agents_server_cwd_by_session`: `session_id`ごとの空でない作業ディレクトリの絶対パス（cwd）。
  `send_message`と`kill`の事前チェックおよびPostToolUse更新に使う。
  欠落時は同じ識別子への成功した`show`応答で、対象自身と`live_child_sessions`のcwdを回復する。
  永続session登録簿の`launcher_session_id`と呼出会話、対象の`launcher_agent_id`と呼出主体を照合する。
  `root_session_id`や変更可能な`owner_agent_id`だけを使う判定は、所有の根拠から除く。
  所有不明・他者・応答ID不一致・空や相対cwdでは回復せず、遮断を保つ。
  案内はshow照会、所有確認後の元操作再発行へ接続する
- `agents_server_sessions`: `session_id`ごとの公開状態および内部状態を保持する。
  生存期間はセッション状態ファイルと同一とする
  - 記録する値: 公開状態として`session_id`、`status`、`kill_requested`、`pending_observation`、`owner_agent_id`、`model_type`、`error`、`agent_message`を保持する。
    内部状態は`turn_id`と`launcher_agent_id`である。
    `pending_observation`は未観測タスクの有無、`owner_agent_id`はそのタスクを発生させた主体、`error`と`agent_message`は終了時のエラーと返却本文を表す。
    `launcher_agent_id`は成功したstartの呼出主体を保存し、send_messageでは既存値を保つ。
    旧レコードで欠けていれば所有不明としてcwd回復の対象から除く
  - `model_type`の決定ロジック: 起動入力で指定された`model_type`を優先し、指定が省略された場合は、`start`の`task`では共通パーサーが読み取る役割文書のfrontmatterから取得し、その他の`mode`では各modeごとのデフォルト種別を適用する。
    ファイル名対応表は過去の旧文書を読み取る互換性維持のためだけに使用する。
    統合前の古い名称で記録されている開始履歴は、対応する`mode`として扱う
  - レコードの生成と更新: `start`の成功レスポンスを受信した時点でレコードを生成し、`send_message`、`kill`、`wait`の各レスポンス受信時、ならびにBash経由で`atk agents wait`が実行された際に更新する
  - 失敗の除外: `observe_tool`の共通境界で、全観測操作の`PostToolUseFailure`、MCPの`isError`・`is_error`、JSON包装内の失敗、`ToolError:`本文を除外する。
    委譲先状態、cwd、root alias、待機登録、終了ログは更新対象から除く。
    正常なshowやkillが返す終了session自身の`error`はツール実行失敗と区別する
  - `pending_observation`を真にする契機: 各開始操作の成功レスポンスを受信した時点、および`delivery`が`reply_started`または`reply_ambiguous`である`send_message`のレスポンスを受信した時点とする。
    なお、`steered`は実行中のターンへ追加指示を届けるだけで`turn_seq`を変えないため、対象外とする。
    呼び出し主体はhookペイロードの`agent_id`（これを持たないメイン会話では`main`）から`owner_agent_id`へ記録する。
    呼び出し主体の判別に`transcript_path`を用いない（`agent-basics:writing-standards/references/claude-hooks.md`「hookスクリプトの基本プロトコル」を参照）
  - `pending_observation`を偽にする契機: `kill`の成功レスポンス（バックグラウンドタスクへの移行通知を含む）を受信した時点と、Bash経由の`atk agents wait`が完了した時点とする。
    `待機中:`表明と`send_message`の受理は観測ではないため、真のまま保持する。
    CLIの`atk agents wait`は引数として特定のセッションを受け取らず、呼び出し主体が所有している既存記録の全セッションを一括して観測済みに更新する。
    偽という値は「現時点で未観測のタスクが存在しない」ことを意味するため、新しいタスクを配送した場合は再び真に戻る。
    `owner_agent_id`は次のタスクが発生するまで保持される
  - 利用用途: Stop判定において、`pending_observation`が真であり、かつ`owner_agent_id`がStopを呼び出した主体と一致している記録を未観測警告の対象とする。
    観測を試みた後の継続は後掲「待機の継続」で別に判定する。
    CLI待機中については、ルートセッションが所有する待機権限が有効であることをStop hookが確認し、警告から除外する。
    現在のセッションがagents_serverのClaude・Codex委譲先である場合に限り、直前の応答の`待機中:`表明が指すセッションもそのStopの判定だけで除外し、保存した観測履歴は変更しない。
    終了工程における委譲先へのwait判定でもこれと同じ読み取り処理を使用し、観測義務と有効な待機状態を区別する。
    なお、`status`が`running`であるレコードの件数自体は、待機をブロックする判定の入力から除く
  - レコードの削除: `stop`ツールの成功レスポンスを受信した時点で、その`session_id`のエントリを削除する。
    `stop`は実行中のターンを持つセッションや未終了のセッションにはエラーを返すため、未完了のセッションの記録は失われない
  - 待機の継続: `pending_observation`が偽でも、同じ呼出主体が所有する待機対象登録に残り、既存のsession登録簿が実行中と解決する対象は、現在の待機・自動再開を別に確認する。
    `agents_server_observations.retained_running_session_ids`が同じwait identity・登録簿を使い、advisorはCLI待機ロックも自動再開もない場合だけ非遮断で案内する。
    判定には既存の状態と登録を再利用する
  - 状態ファイルに保持しない情報: 結果を回収済みであることを示す専用フラグや、thread IDをハッシュ化した状態ファイルなどは保持しない
