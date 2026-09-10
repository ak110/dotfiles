# セッション状態フラグ

配布物のhook間で共有する状態の記録元、利用先、寿命を定める。
状態ファイルは`{tempdir}/claude-agent-toolkit-{session_id}.json`とする。
計画名の再出力抑止記録は`{tempdir}/claude-agent-toolkit-session-title/{session_id}.json`へ分離する。

フックがセッション状態の不足を理由にブロックした場合は、状態ファイルを`Read`して
当該状態と他の記録済み状態を実測する。
記録契機が発生していない場合は、同じ委譲の再実行以外の対処を選ぶ（努力目標）。

## 検証・git状態系

- `inherited_from_session_id`: 背景化などで現行`session_id`の状態が不在となった場合に、一意に特定した前身`session_id`を記録する。共通hook入口が前身の全状態キーと同時に1回だけ書き込み、現行状態が存在する間は再継承しない

- `test_executed`: PostToolUseがBashの検証コマンドまたはpyfltr MCPの`run_for_agent`成功時に記録し、
  `git commit`未検証警告の抑制に使う。セッション終了まで保持する。
  Bash経由の記録元は、失敗した実行でPostToolUseが発火しないClaude Codeに限る
- `git_log_checked`: PostToolUse(Bash)が`git log`観測時に記録する。
  Bash経由の記録元は`test_executed`と同じくClaude Codeに限る
- `amend_pending_status_check`: cwd別辞書としてamendまたはfixup成功時に記録し、push前のdirty検査に使う。
  Bash経由の記録元は`test_executed`と同じくClaude Codeに限る
- `sleep_poll_detected`: PreToolUse(Bash)が入れ子でなく早期離脱のない`for`・`while true`・`while :`本体のsleepポーリング、又は対象外のsleep直後の状態確認連結を検出した場合に記録する。入れ子ループは判定対象外とする
- `recursive_grep_detected`: PreToolUse(Bash)が除外設定を反映しない再帰`grep`のディレクトリ実行を検出した場合に記録する。1件目は警告に留め、同一セッションの2件目から当該呼び出しを遮断する
- `session_edited_files`: PostToolUseが成功した編集の対象パスを重複なく記録し、
  PreToolUse(Bash)の一括stage警告が自セッション編集済み集合として読む。セッション終了まで保持し、
  リセット経路は設けない。Claude CodeではWrite・Edit・MultiEditが、Codexでは成功した`apply_patch`が
  記録元となる。Codexの移動では移動元と移動先の双方を記録する。
  CodexのBashは終了コードを取得できないため、`test_executed`・`git_log_checked`・
  `amend_pending_status_check`と同様に本フラグの記録元にならない

## 応答言語系

- `english_warning_count`: `agent-toolkit/agent_toolkit/_hooks/pretooluse/agent_checks.py`が、直前のアシスタント応答の地の文を英語主体と判定した連続ターン数を記録する。
  同フックが遮断への昇格判定に読む。英語主体でないと判定した回で0へ戻し、遮断した回で1へ設定する。セッション終了まで保持する
- `english_warning_msg_id`: 同フックが、直前に通知したアシスタント応答のmessage IDを記録する。
  同じ応答に対する重複した通知と連続ターン数の二重加算を抑止する入力として同フックが読む。
  セッション終了まで保持し、リセット経路は設けない

## plan系

- `working_plan_save_notified`: 計画作業rootに残る計画バンドルの保存確認をStopフックが促した事実を記録する。`agent-toolkit/agent_toolkit/_hooks/plan_save_advisor.py`が記録し、同フックが再通知の抑止に読む。セッション終了まで保持し、リセット経路は設けない
- `plan_mode_skill_invoked`: plan-mode起動を記録し、計画ファイル検査の適用判定に使う
- `current_plan_file_path`: 計画ファイル編集時のパスを記録する
- `last_hook_session_title`: Claude CodeのUserPromptSubmitが計画ファイルのstemを`sessionTitle`へ実際に出力した値を記録する。
  値が存在する間は同一セッションで再出力しない。通常状態JSONへ複製せず、専用の排他ロック下で
  再出力抑止記録へ先に保存できた呼び出しだけが出力する。通常状態の期限回収と通常のSessionEndでは保持し、
  終了理由が`clear`の場合だけ通常状態、再出力抑止記録及び双方のロックを削除する
- `plan_file_written`・`direct_agent_toolkit_edit_count`・`last_agent_toolkit_edit_path`:
  計画ファイル作成前の直接編集を検知する

## 振り返り・モード系

- `agent_toolkit_edit_skill_invoked`: dotfilesリポジトリ固有の
  `pytools/claude_hook/pretooluse.py`などがagent-toolkit-edit起動を記録し、編集警告の抑制に使う
- `dotfiles_reference_docs_read`: dotfilesの個人PostToolUseフックが参照文書へのReadを解決済み絶対パスの一覧として記録し、
  個人PreToolUseフックが同じチェックアウト内のコーディングエージェント向け文書の編集警告を抑制する。
  セッション終了まで保持し、リセット経路は設けない
- `process_wi_skill_invoked`: process-wiスキルの起動を記録する。
  PostToolUse(Skill)とUserPromptSubmitが記録し、`agent-toolkit:exit-session`起動時に偽へ戻す。セッション終了まで保持する
- `autonomous_exit_invoked`: `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`が`agent-toolkit:exit-session`の成功したSkill呼び出しを記録し、
  `agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`がprocess-loopのStop判定で参照する。セッション状態の有効期間中だけ保持し、通常のスキル完了処理で再利用しない
- `last_user_prompt_at`: `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が通常のユーザー発話を受領した時刻をPOSIX秒で記録する。
  同フックが、直前の通常発話からの経過時間で照合指示の注入要否を判定する入力として読む。
  ハーネスが挿入した通知とコマンド起動では記録も注入もしない。セッション終了まで保持し、リセット経路は設けない

## 通知反復系

- `warn_notice_counts`: `warn`区分の通知を生成した検査の原因識別子ごとの累積件数を記録する。
  キーは`<hook_id>|<原因識別子>`、値は当該セッションでの発生件数とする。
  通知の整形処理が記録元であり、同じ処理が3件目以降の通知本文へ反復の旨と累積件数を載せる判定に読む。
  セッション終了まで保持し、リセット経路は設けない

## agents_server連携系

- `agents_server_cwd_by_session`: `session_id`ごとの絶対`cwd`を記録し、`send_message`と`kill`の検査及び各ツールのPostToolUse状態更新に使う
- `agents_server_sessions`: `session_id`ごとに公開状態と内部状態を記録する。公開状態は当該sessionの`session_id`・`status`・`kill_requested`・`pending_observation`・`owner_agent_id`・`model_type`・`error`・`agent_message`とする。`pending_observation`は観測を試みていない作業の有無を示し、`owner_agent_id`は当該作業を発生させた主体を示す。`model_type`は起動時の応答から得た値とし、`error`と`agent_message`は終端時の値とする。内部状態は`turn_id`とする。記録は`start`・`start_explore`・`start_shell`の成功応答で生成し、`wait`・`send_message`・`kill`の応答境界と、Bash経由の`atk agents-wait`実行時に更新する。`pending_observation`は`start`・`start_explore`・`start_shell`の成功応答で真になる。`send_message`の応答では、`delivery`が`reply_started`又は`reply_ambiguous`である場合だけ真にする。真にした呼出主体はhook payloadの`agent_id`から`owner_agent_id`へ記録する。`delivery`が`steered`である応答では真にしない。steerは実行中のturnへ追加指示を配送するだけで`turn_seq`を変えず、当該turnの終端は当該turnに対する既存の観測が待つためである。`agent_id`を持たないメイン会話は`main`とする。`transcript_path`はサブエージェント内で発火したフックでもセッション本体の記録を指すため、呼出主体の判別に使わない。`wait`の応答、`kill`の成功応答及びBash経由の`atk agents-wait`実行では`pending_observation`を偽にし、`owner_agent_id`は次の作業発生まで保持する。`wait`は`status`を問わず偽にする。委譲先が稼働中のまま待機表明でターンを終える正常終端を警告しないためである。`wait`又は`kill`の呼び出しが実行環境により背景タスクへ移り、構造化応答を伴わない移行通知だけが返った場合も、当該通知の受領時に`pending_observation`を偽にする。対象sessionはMCP呼び出しでは入力の`session_id`、CLIでは`atk agents-wait`の位置引数から解決し、既存の記録が無い場合は新規に作成しない。呼び出しの受理をもって観測を試みたものとして扱うためである。sessionを一度でも観測したかという履歴ではないため、偽になった後に新しい作業を配送すれば再び真になる。寿命はセッション状態ファイルと同じとする。利用先はStop判定であり、`pending_observation`が真で`owner_agent_id`がStopの呼出主体と一致する記録だけを警告へ使う。責任主体を記録していない旧形式の記録は警告対象にしない。結果を回収済みであることを示す状態は持たない。thread IDをハッシュ化した状態ファイルは作成しない
- `agents_server_list_fingerprint`・`agents_server_list_blocked_at`: PreToolUseが`agents_server`の`list`直前に、`agents_server_sessions`をキー順JSONへ正規化した指紋と直近の遮断時刻を記録する。同じ指紋での2回目の`list`を遮断し、遮断から5分以内の再実行では時刻を削除して通過させる。記録元と利用先は`agent_checks._check_agents_server_list_repeat`だけとし、寿命はセッション状態ファイルと同じとする。リセットは遮断直後の再実行だけで行う
- `observed_required_reads`: 規範が全文読解を要求する文書のうち、同一セッションで全文読取を観測した文書の論理名を文字列の配列として保持する。記録はPostToolUseが行い、`Read`の`file_path`を実行ホストのパス規則で正規化した絶対パスが、稼働中のplugin rootから組み立てた当該文書の絶対パスと一致し、かつ`offset`と`limit`のいずれも指定されていない呼び出しだけを観測とする。別の作業ツリー又は別の複製にある同名の文書、部分読取及びシェル経由の読取は観測としない。利用先はPreToolUseの`AskUserQuestion`検査であり、当該配列に無い文書がある場合に当該ツール呼び出しを遮断する。寿命はセッション状態ファイルと同じとする

`wait`の応答境界とBash経由の`atk agents-wait`では、呼出主体が所有する全sessionの`pending_observation`を偽にする。`wait`が返した選択済みsessionの公開状態は応答の`session_id`と`status`から更新する。`status`が`running`である記録の件数を根拠として待機を遮断する検査は持たない。`stop`の成功応答を受領した場合は、当該`session_id`のエントリーを本キーから除去する。`stop`は実行中turnを持つsessionと非終端のsessionを拒否するため、当該応答は当該sessionが終端済み、期限切れ又は既破棄のいずれかであることを含意し、除去により未終端のsessionの記録が失われることはない。

## 背景タスク系

- `background_task_ids`: PostToolUse(Bash)が`run_in_background`指定の応答から取得したタスクIDを重複なく記録する。
  PreToolUse(TaskStop)が、停止対象が自セッションの起動した背景タスクかを判定する入力として読む。
  セッション終了まで保持し、リセット経路は設けない
- `task_stop_blocked_at`: PreToolUse(TaskStop)が遮断した時刻のPOSIX秒を記録し、同フックが再実行許可窓の判定に読む。
  セッション終了まで保持し、リセット経路は設けない

## UWI系

- `uwi_answered_by_repo`: エージェント識別子ごと・対象リポジトリIDごとの回答済みUWIファイル名を
  PostToolUseが記録する。値は`{エージェント識別子: {対象リポジトリID: ファイル名一覧}}`の2段辞書とし、
  hook payloadの`agent_id`を持たないメイン会話は`main`をキーとする。
  cwdが空・保存先未解決・後述の指紋が前回と同一・リポジトリID未解決・走査不完全のいずれかの場合は
  記録を更新しない。前回の一覧にない回答済みファイル名の通知判定に使い、セッション終了まで保持する。
  リセット経路は設けず、一覧の記録更新のみで状態を進める。
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
新規フラグには記録元、利用先、寿命、リセット経路を併記する。
状態JSONはセッション終了イベントを契機に削除しない。同イベントは同じセッションへ後から戻る場合にも発火し、
`--continue`・`--resume`・`/resume`で戻ると同じ`session_id`で会話が続くため、削除すると再開後の記録が失われる。
回収は更新から一定期間が経過した通常状態と計画名の再出力抑止記録に限る。
ロックファイルはどの経路でも削除せず、一時ディレクトリの通常の回収に委ねる。
例外は終了理由が`clear`の場合とし、会話が破棄されたことが確定するため排他ロック下で当該セッションの
通常状態と再出力抑止記録を削除する（ロックは同じく削除しない）。
サブエージェント側で記録される状態は呼び出し元へ自動伝播しないため、
親側で必要な値は完了報告の構造化欄から厳格に解析する。
