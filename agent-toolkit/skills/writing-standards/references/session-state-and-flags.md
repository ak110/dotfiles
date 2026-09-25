# セッション状態ファイルとフラグ

配布物のhook間で共有する状態ファイルの設計、寿命、排他制御及び個別フラグの記録元と利用先を定める。
状態ファイルは`{tempdir}/claude-agent-toolkit-{session_id}.json`とする。
計画名の再出力抑止記録は`{tempdir}/claude-agent-toolkit-session-title/{session_id}.json`へ分離する。

## 状態ファイルの設計

Claude CodeまたはCodexのhook間で情報を共有する場合、セッション単位の状態ファイルを使う。
hookは1呼び出しごとに独立プロセスとして起動するため、情報の引き継ぎには状態ファイルを使う。

- パス規則: 本書冒頭が定めるパスを`tempfile.gettempdir()`と`payload["session_id"]`から組み立てる
- 形式: 単一のJSONオブジェクト。フラグ名はsnake_caseで統一する
- 書き込み: PostToolUseで観測したイベント（スキル呼び出し・背景タスクの起動など）をフラグとして記録する
- 読み取り: PreToolUse・UserPromptSubmit・Stopで判定材料として参照する（例: 所有外TaskStopの遮断・計画名の表示・終了前の未観測作業の通知）
- 破損・不在時: 空辞書として扱い、安全側の判定にフォールバックする
- `session_id`の切替え: 現行状態が無い場合だけtranscriptを先頭から走査し、状態ファイルを持つ別の`session_id`が一意なら全キーを継承する。継承元を状態へ記録し、現行状態がある通常時は状態ファイルの存在確認だけで終える
- 設計原則: フックイベント間の多段同期（コマンド文字列の完全一致検出とハッシュ値比較の組合せ等）は状態ファイルの外へ置く。
  対象の確認はファイル実体への直接実行・直接読み取りで代替する。
  構造化した記録は、記録元と利用先が同じフックの内側で閉じる場合に限って持つ
- フラグの用途・書き込み元・読み取り元の対応表をプラグインごとにドキュメント化する。
  `agent-toolkit`自身の一覧と、同じ状態ファイルを共有する個人フックのフラグの一覧は、本書の後掲の各節で定める
- 通常状態の期限より長く保持する記録は通常状態JSONへ混在させず、用途別の保存先と排他ロックへ分離する。
  `agent-toolkit`の計画名再出力抑止記録は本書冒頭が定める分離先を使う

### 完了報告からの状態読み取り

完了報告本文から機械的に状態を読み取る場合、本文中の引用と区別できる位置へ
記録行を置く。検出対象は完了報告末尾の連続した記録行ブロック、または最終行へ限定する。
検出位置を限定しないと、自由記述本文中の引用が状態フラグを
誤って真化させ、完了判定が機能不全に陥る。
検出対象はこの2箇所に限る。記録行の判定パターンは既知のキー
（例: `invoked_subagents`・`codex_unavailable`）へ限定する許可リスト方式とし、
英字キー全般に一致する汎用パターンは許可リストの外に置く。汎用パターンは、末尾付近に
他の`キー:`形式の散文が続く場合に引用ごと記録行と誤認する

### 並行書き込みの排他制御

Claude Codeは並列ツール呼び出しでhookを同時発火するため、複数プロセスから同一の状態ファイルへ書き込みが競合する。

- 通常状態の更新は排他ロック付きの`update_state`、計画名の記録は専用の`claim_session_title`を経由し、公開するAPIをこの2つに限る
- 前身状態の継承は現行セッションの排他ロック下で不在を再確認し、並行するhookが同時に継承しないようにする
- ロックファイルは常に保持する。内容を持たない空ファイルであり、一時ディレクトリの通常の回収に委ねる
- 並行書き込みの回帰テストを維持する

## 状態の継承

- `inherited_from_session_id`: 背景化などで現行`session_id`の状態が不在となった場合に、一意に特定した前身`session_id`を記録する。共通hookの起動処理が前身の全状態キーと同時に1回だけ書き込み、継承は現行状態が不在の場合だけ行う

## 応答言語系

- `english_warning_count`: `agent-toolkit/agent_toolkit/_hooks/pretooluse/agent_checks.py`が、直前のアシスタント応答の地の文を英語主体と判定した連続ターン数を記録する。
  同フックが遮断への昇格判定に読む。英語主体でないと判定した回で0へ戻し、遮断した回で1へ設定する。セッション終了まで保持する
- `english_warning_msg_id`: 同フックが、直前に通知したアシスタント応答のmessage IDを記録する。
  同じ応答に対する重複した通知と連続ターン数の二重加算を抑止する入力として同フックが読む。
  セッション終了まで保持し、リセット処理は設けない

## plan系

- `working_plan_save_notified`: 計画作業rootに残る計画バンドルの保存確認をStopフックが促した事実を記録する。`agent-toolkit/agent_toolkit/_hooks/plan_save_advisor.py`が記録し、同フックが再通知の抑止に読む。セッション終了まで保持し、リセットする手段は設けない
- `plan_mode_skill_invoked`: plan-mode起動を記録し、計画構造の自動チェックの適用判定に使う
- `current_plan_file_path`: PostToolUseが計画ファイルの編集時と公開された計画ファイル作成コマンドの出力からパスを記録し、PreToolUse(Skill)がplan-mode起動時に消去する。UserPromptSubmitが計画名の`sessionTitle`出力に読む
- `last_hook_session_title`: Claude CodeのUserPromptSubmitが計画ファイルのstemを`sessionTitle`へ実際に出力した値を記録する。
  出力は値が存在しない間の1回に限る。通常状態JSONへ複製せず、専用の排他ロック下で
  再出力抑止記録へ先に保存できた呼び出しだけが出力する。通常状態の期限回収と通常のSessionEndでは保持し、
  終了理由が`clear`の場合だけ通常状態、再出力抑止記録及び双方のロックを削除する

## 振り返り・モード系

- `process_wi_skill_invoked`: process-wiスキルの起動を記録する。
  PostToolUse(Skill)とUserPromptSubmitが記録し、`atk agents-exit-session`の機械可読な応答を受領した時点で偽へ戻す。セッション終了まで保持する
- `autonomous_exit_invoked`: `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`が`atk agents-exit-session`の実行と機械可読な応答を記録し、
  `agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`がprocess-loopのStop判定で参照する。保持はセッション状態の有効期間中に限り、通常のスキル完了処理では再利用の対象外とする
- `stop_no_tool_turn_count`: `agent-toolkit/agent_toolkit/_hooks/busy_loop_guard.py`が、自セッションのツール呼び出しを含まないターンの連続回数を記録する。
  同フックが常駐ループの停止判定の入力として読む。ツール呼び出しを観測したターンと、委譲先又は背景ジョブの完了待ちのターンで0へ戻し、停止工程を実行した時点でも0へ戻す。セッション終了まで保持する
- `stop_observed_entry_count`: 同フックが、Stop判定の時点で観測済みの会話記録のエントリ数を記録する。
  同フックが次のターンで増分だけを走査する起点として読む。セッション終了まで保持する
- `last_user_prompt_at`: `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が通常のユーザー発話を受領した時刻をPOSIX秒で記録する。
  同フックが、直前の通常発話からの経過時間で`照合注記`（発話の内容を現物で確かめる手順を示す注記）の注入要否を判定する入力として読む。
  記録と注入の対象は、自動的なプロンプトを除く全てのユーザー発話とする。ユーザー自身が入力したスラッシュコマンドも対象に含め、機械注入ターンは対象の外に置く。
  機械注入ターンの判定入力は5系統とする。第1にpayloadの`source`が`user`以外であること。第2に`prompt`の1行目が`<agent-toolkit-auto-inserted source="agent-toolkit/periodic-recheck" kind="periodic-recheck">`だけの行であること。第3に委譲先として起動されていること。第4に`prompt`が`<task-notification`又は旧形式の`<cross-session-message`で始まること。第5に`prompt`の1行目が`<agent-toolkit-auto-inserted`要素の開始タグを含むこと。
  セッション終了まで保持し、リセット処理は設けない

## 通知反復系

- `unregistered_managed_temp_fingerprint`: `atk`の共通エントリポイントが、登録を持たない管理対象の絶対パス集合を安定順で正規化した指紋を記録する。同じセッションで同じ集合を報告済みの場合は警告を省略し、集合が変化した場合は再度警告する。寿命はセッション状態ファイルと同じとする
- `warn_notice_counts`: `warn`区分の通知を生成した診断処理の原因識別子ごとの累積件数を記録する。
  キーは`<hook_id>|<原因識別子>`、値はそのセッションでの発生件数とする。
  通知の整形処理が記録元であり、同じ処理が3件目以降の通知本文へ反復の旨と累積件数を載せる判定に読む。
  診断処理が要旨を渡した通知では、同じ件数を用いて2件目以降の本文を要旨と件数だけへ短縮する。
  セッション終了まで保持し、リセットする手段は設けない

## agents_server連携系

- `agents_server_cwd_by_session`: `session_id`ごとの絶対`cwd`を記録し、`send_message`と`kill`の事前判定及び各ツールのPostToolUse状態更新に使う。`show`の応答が返す稼働中の子sessionの識別子と`cwd`の対も同じキーへ記録し、呼び出し元がその子sessionへ追送と打ち切りを発行できる状態にする
- `agents_server_sessions`: `session_id`ごとに公開状態と内部状態を記録する。公開状態はそのsessionの`session_id`・`status`・`kill_requested`・`pending_observation`・`owner_agent_id`・`model_type`・`error`・`agent_message`とする。`pending_observation`は観測を試みていない作業の有無を示し、`owner_agent_id`はその作業を発生させた主体を示す。`model_type`は専用`start`ではタスク文書名、`start_custom`では起動入力から解決し、`error`と`agent_message`は終端時の値とする。内部状態は`turn_id`とする。記録は`start`・`start_custom`・`start_explore`・`start_write`・`start_shell`の成功応答で生成し、`send_message`・`kill`・`wait`の応答境界と、Bash経由の`atk agents wait`実行時に更新する。`pending_observation`は各開始操作の成功応答で真になる。`send_message`の応答では`delivery`が`reply_started`又は`reply_ambiguous`である場合だけ真にする。真にした呼出主体はhook payloadの`agent_id`から`owner_agent_id`へ記録する。`delivery`が`steered`である応答では真にしない。steerは実行中のturnへ追加指示を配送するだけで`turn_seq`を変えず、そのturnの終端はそのturnに対する既存の観測が待つためである。`agent_id`を持たないメイン会話は`main`とする。`transcript_path`はサブエージェント内で発火したフックでもセッション本体の記録を指すため、呼出主体の判別に使わない。`kill`の成功応答及びBash経由の`atk agents wait`完了時は`pending_observation`を偽にし、`owner_agent_id`は次の作業発生まで保持する。CLI待機中はルートセッションが所有する待機所有権の生存をStopフックが確認し、`pending_observation`が真でも未観測警告の対象から除外する。CLIの`atk agents wait`は入力sessionを取らず、呼出主体が所有する全sessionを観測済みにする。更新の対象は、既存の記録を持つsessionに限る。呼び出しの受理をもって観測を試みたものとして扱うためである。sessionを一度でも観測したかという履歴ではないため、偽になった後に新しい作業を配送すれば再び真になる。寿命はセッション状態ファイルと同じとする。利用先はStop判定であり、`pending_observation`が真で`owner_agent_id`がStopの呼出主体と一致する記録だけを警告へ使う。警告の対象は、責任主体を記録した形式の記録に限る。結果を回収済みであることを示す状態は持たない。thread IDをハッシュ化した状態ファイルは作成しない
`wait`の応答境界とBash経由の`atk agents wait`では、呼出主体が所有する全sessionの`pending_observation`を偽にする。`wait`が返した選択済みsessionの公開状態は応答の`session_id`と`status`から更新する。`status`が`running`である記録の件数は、待機の遮断の入力の外にある。`stop`の成功応答を受領した場合は、その`session_id`のエントリーを本キーから除去する。`stop`は実行中turnを持つsessionと非終端のsessionを拒否するため、その応答はそのsessionが終端済み、期限切れ又は既破棄のいずれかであることを含意し、除去により未終端のsessionの記録が失われることはない。

## 背景タスク系

- `background_task_ids`: PostToolUseが、自セッションのツール呼び出しの応答から取得したタスクIDを重複なく記録する。
  記録の契機は、Bashの`run_in_background`指定が成功した応答、同じ指定が失敗した応答、
  およびツール種別を問わない背景移行通知の3つとする。所有の根拠は自身の呼び出しが識別子を返したことであり、
  その呼び出しの成否に依存しない。
  PreToolUse(TaskStop)が、停止対象が自セッションの起動した背景タスクかを判定する入力として読む。
  セッション終了まで保持し、リセット処理は設けない
- `background_task_output_paths`: PostToolUseが背景移行応答から得たタスクIDをキー、絶対出力パスを値として記録する。
  `run_in_background=true`の応答と、実行時間上限によるホストの背景移行応答を同じ形式で扱う。
  PreToolUse(Bash)は`stop_gate.py`と同じ起動集合と完了集合を使って未完了のタスクだけを選び、
  対応する出力パスを読取コマンドのオペランドとして渡した場合に完了通知待ちを案内する。
  完了通知がUserPromptSubmitへ到達した時点で、同通知のタスクIDに対応する要素を削除する。
  未完了の対応はセッション終了まで保持する
- `task_stop_blocked_at`: PreToolUse(TaskStop)が遮断した時刻のPOSIX秒を記録し、同フックが再実行許可窓の判定に読む。
  セッション終了まで保持し、リセット処理は設けない
- `stall_detection_completed_at_by_task`: `record_stall_detection.py`が停滞検知を完了した停止対象の完全なタスクIDをキー、完了時刻のPOSIX秒を値として記録する。
  PreToolUse(TaskStop)は5分以内の一致する対象だけを停止可能と判定し、期限切れ要素を除去する。
  成功したPostToolUse(TaskStop)が一致要素を消費する。停止の失敗時は同じ5分窓で再試行できるよう保持する

## UWI系

- `uwi_answered_by_repo`: エージェント識別子ごと・対象リポジトリIDごとの回答済みUWIファイル名を
  PostToolUseが記録する。値は`{エージェント識別子: {対象リポジトリID: ファイル名一覧}}`の2段辞書とし、
  hook payloadの`agent_id`を持たないメイン会話は`main`をキーとする。
  cwdが空・保存先未解決・後述の指紋が前回と同一・リポジトリID未解決・走査不完全のいずれかの場合は
  記録を更新しない。前回の一覧にない回答済みファイル名の通知判定に使い、セッション終了まで保持する。
  リセット処理は設けず、一覧の記録更新のみで状態を進める。
  メインとサブエージェントのフック呼び出しは同一`session_id`で届くため、
  エージェント識別子で分けないと一方の呼び出しが回答差分を消費し他方へ通知が届かない

- `uwi_fingerprint_by_repo`: active状態ディレクトリ（`inbox`・`processing`）の内容変化指紋を
  PostToolUseが記録する。値は`{エージェント識別子: {作業ディレクトリ: 指紋文字列}}`の2段辞書とする。
  前回観測時の指紋と同一の場合は走査と回答差分の検出をいずれも省略する用途に使う。
  回答済みファイル名一覧と同じ更新で記録し、セッション終了まで保持する

上記2つのキーは、現行のキーが無い場合だけ改名前のキー`tbd_answered_by_repo`・`tbd_fingerprint_by_repo`の
内容を読み取って引き継ぐ。配布をまたいで稼働し続けるセッションが改名前のキーで基準値を持つためであり、
書き込みは常に現行のキーへ行う。

サブエージェント起動の判定は`tool_name in ("Agent", "Task")`をSSOTとする。
新規フラグには記録元、利用先、寿命、リセット処理を併記する。
状態JSONの削除契機は、後掲の期限回収と終了理由が`clear`の場合に限る。同イベントは同じセッションへ後から戻る場合にも発火し、
`--continue`・`--resume`・`/resume`で戻ると同じ`session_id`で会話が続くため、削除すると再開後の記録が失われる。
回収は更新から一定期間が経過した通常状態と計画名の再出力抑止記録に限る。
ロックファイルはいかなる場合も削除せず、一時ディレクトリの通常の回収に委ねる。
例外は終了理由が`clear`の場合とし、会話が破棄されたことが確定するため排他ロック下でそのセッションの
通常状態と再出力抑止記録を削除する（ロックは同じく削除しない）。
サブエージェント側で記録される状態は呼び出し元へ自動伝播しないため、
親側で必要な値は完了報告の構造化欄から厳格に解析する。
