# セッション状態ファイルとフラグ

配布物のhook間で共有する状態ファイルの設計、寿命、排他制御および個別フラグの記録元と利用先を定める。
状態ファイルは`{state_dir}/hook-sessions/claude-agent-toolkit-{session_id}.json`とする。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-stop.md#「終了工程の証拠」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks-stop.md#「終了工程の証拠」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/publish-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/publish-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/termination-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks.md#「hookスクリプトの基本プロトコル」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks.md#「hookスクリプトの基本プロトコル」` |

## 状態ファイルの設計

hookは呼び出しごとに独立プロセスとして起動するため、Claude CodeまたはCodexのhook間で情報を共有する場合はセッション単位の状態ファイルを使う。

- パス規則: 本書冒頭のパスを`_common/state_paths.py`の`state_dir()`と`payload["session_id"]`から組み立てる
- 置き場所は必要な寿命と共有範囲で選び、再開・ホスト再起動後の判定に読む記録と、その排他に使うロックは同じ状態ディレクトリへ置く。一時ディレクトリへ置けるのは、失われても判定に必要な情報を失わない診断ログ・再生成可能なキャッシュなどとする
- 新位置に状態が無い場合だけ、排他ロック下で同じ`session_id`の旧位置（`tempfile.gettempdir()`配下）のJSONを新位置へ移す。通常の読取・更新・期限回収・前身状態の継承は新位置を使う
- 形式: 単一のJSONオブジェクト。新規フラグ名はsnake_caseとし、既存のフラグ名は読み取り側との契約としてそのまま保つ
- 書き込みはPostToolUseで観測したイベント（スキル呼び出し・バックグラウンドタスクの起動など）、読み取りはPreToolUse・UserPromptSubmit・Stopの判定材料とする
- 破損・不在時は空辞書として扱い、安全側の判定にフォールバックする
- `session_id`の切替え: 現行状態が無い場合だけtranscriptを先頭から走査し、状態ファイルを持つ別の`session_id`が一意なら全キーを継承して継承元を記録する。現行状態がある通常時は存在確認だけで終える
- フックイベント間の多段同期（コマンド文字列の完全一致とハッシュ値比較の組合せ等）は状態ファイルへ持ち込まず、ファイル実体への直接実行・直接読み取りで確かめる。構造化した記録は、記録元と利用先が同じフックの内側で閉じる場合に限る
- フラグの用途・書き込み元・読み取り元・寿命・リセット処理を本書の後掲の各節へ記載し、新規フラグも併記する（努力目標）。同じ状態ファイルを共有する個人フックのフラグも同様とする
- 状態JSONの削除契機は、期限回収と、終了理由が`clear`のSessionEndに限る。SessionEndは`--continue`・`--resume`・`/resume`で同じ`session_id`へ戻る場合にも発火するため、他の契機で削除すると再開後の記録が失われる。期限回収の対象は更新から一定期間が経過した通常状態とし、`clear`では排他ロック下でそのセッションの新旧両位置の状態を削除する
- サブエージェント起動の判定は`tool_name in ("Agent", "Task")`をSSOTとする。サブエージェント側の状態は委譲元へ伝播しないため、親側で必要な値は完了報告の構造化欄から厳格に解析する

### 並行書き込みの排他制御

Claude Codeは並列ツール呼び出しでhookを同時発火するため、同一の状態ファイルへの書き込みが競合する。

- 状態の更新は排他ロック付きの`update_state`へ集約し、命名用の種別と自動設定した名前も同じAPIで保存する
- 通常のロックは`{state_dir}/hook-sessions/.state.lock`の固定1個とし、`TMPDIR`が異なるプロセスも同じロックで排他する
- ロックは短い状態の読取・変更・書込で保持し、長いtranscriptの読取と継承候補の準備はロック外で行う
- 前身状態の継承は候補の準備後に同じ共有ロック下で現行状態の不在を再確認し、二重の継承を防ぐ
- `clear`と期限回収は同じ共有ロック下でJSONを削除し、固定ロックは保持する
- 旧一時ディレクトリのロックは、旧JSONの移行に必要な範囲だけで使う
- 並行書き込みの回帰テストを維持する

## セッションの活動記録

`_common/session_activity.py`はOSアカウントの状態ディレクトリ配下の`session-activity/{session_id}.json`へ会話ごとの最新状態を保存する。
状態ディレクトリの解決元は`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir()`である。
`hook.py`が受け取る既存イベントの処理に活動記録を加え、`submission_activity`がprocess-loopの開始前判定へ、対象リポジトリへ投入中のWI投入作業の活動を渡す。

- 保存する項目は版数、会話ID、実行系、PIDと起動時刻、会話内の各エージェントの活動状態、親会話ID、セッション記録のパス、バックグラウンドタスクの有無および更新時刻とする。
- SessionStart・UserPromptSubmit・ツールイベント・SubagentStartで活動を記録し、Stop・SubagentStopでは実際のhook応答が続行を求めるかを反映する。
- 応答待ちの除外には、セッション記録のターン終了またはホスト登録のidle状態と、稼働する子がないことを使う。
- SessionEndは活動JSONを削除し、記録されたPIDと起動時刻が生存プロセスに一致しない場合は取得側で失効させる。
- 更新と削除は固定の`activity.lock`の排他下で行い、会話ごとの履歴や終了した子の項目を残さない。
- 読取不能な活動記録は活動候補として返す。会話を解決できない生存プロセスは、どの作業にも属さないため活動の集計から除く。
- WI投入作業の属性は`session-activity/submissions/{session_id}.json`へ会話ごとに保存する。項目は版数、会話ID、作業種別（`add-awi-by-user`）、正規化した投入先URLの一覧、更新時刻とする。`atk wi submission begin`が作成・置換し、`atk wi submission end`が削除する。hookのSessionEndは削除せず、再開した会話で属性を再び有効にする。
- 開始前判定は、投入先がprocess-loopの対象リポジトリ集合と交わる属性を持つ会話とその子孫の活動だけを理由にし、属性を読めない会話と属性のない会話を除く。
- 除外する会話とその子孫は`_common/session_parents.py`の親子解決を使い、process-loopの共通環境印だけで別ループの会話を除外しない。

## managed-tempの登録情報

managed-tempのマーカーファイル（`.agent-toolkit-managed-temp.json`）とOSアカウント専用の登録簿は、同じ版数付きの登録情報を保持する。
スキーマ版数3は`prefix`・`created_at`・`awis`を必須とし、全項目の完全一致を検証する。版数2は`prefix`と`created_at`を必須とする版数2のフィールド集合どうし、版数1は版数1のフィールド集合どうしだけを完全一致で検証する。
版数の不一致、片側だけの更新、必須項目の欠落、型不正、余分な項目は拒否する。

## 完了報告からの状態読み取り

完了報告本文から機械的に状態を読み取る場合、検出対象は完了報告末尾の連続した記録行ブロックか最終行に限る（本文中の引用が状態フラグを誤って真にするのを防ぐ）。
記録行の判定は既知のキー（例: `invoked_subagents`・`codex_unavailable`）の許可リストとし、英字キー全般に一致する汎用パターンを使わない（末尾付近の`キー:`形式の散文を記録行と誤認する）。

## Function hooksの終了要求

Claude Codeが会話イベントの処理を登録するために読み込むプラグインのモジュールをFunction hooks module（以下「mod」）と呼ぶ。このmodとPythonのCLI・Stop hookは、同じセッションIDの読込目印と終了要求を共有する。置き場所は絶対パスの`CLAUDE_CONFIG_DIR`、無い場合は`HOME`、次に`USERPROFILE`の`.claude`の下の`agent-toolkit-function-hooks/`とし、Python側の一時ディレクトリ解決は使わない。そこへ`marker-{session_id}.txt`と`request-{session_id}.txt`を置く。セッションIDは英数字、アンダースコア、ハイフンだけを受理する。

`session.start`は読込目印へ`ready`を書き、終了要求を`consumed`へ初期化する。Pythonは目印が`ready`で、更新時刻が現在のClaude Code本体の開始より後の場合だけ、終了要求へ`requested`を書いて`exit_requested`を返す。`turn.complete`はメインのターンだけでその値を読み、`consumed`へ書き換えた後に`/exit`をキューへ入れる。目印が無い場合とCodexでは、対話CLI本体を従来の方法で終了させる。

`/exit`はセッション限りの作業が残ると確認画面を表示するため、`turn.complete`は`/exit`の直前に`durable`が真でないcron taskを削除する。一覧の取得と各削除は`$.tool.check`の判定が`allow`の場合だけ行い、失敗しても`/exit`は実行する。
process-loopが起動した会話の最上位のStopでは、終了要求が`requested`かつStop入力の`background_tasks`に有効な非`teammate`の作業が残る場合を判定する。その場合は`agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`が終了要求を`consumed`へ戻してblockする。Stopは同じターンの`turn.complete`より先に発火するため、`/exit`は取り下げた要求では実行されずに済む。

Stop hookの無進捗停止（`busy_loop_guard.py`）の終了要求は、Stopの全判定の後に共通ハンドラー（`stop.py`）が発行する。要求が`requested`か単一PIDへの終了要求として成立した場合、同じStopの応答は停止の通知だけになる。このため`autonomous_exit_invoked`が偽のままでも、無進捗停止による終了要求はそのまま`turn.complete`で消費される。`autonomous_exit_invoked`が記録するのは`atk agents-exit-session`の実行だけであり、Stop内で発行した終了要求はこのフラグとは別に終了要求のファイルが表す。

通常のSessionEndでは現行セッションの読込目印・終了要求・後述の定期再確認の判定と利用上限の記録ファイルを保持して、期限を過ぎた他セッションのファイルを回収する。`clear`では現行セッションの4ファイルも除く。保持期限と回収は`agents_exit_session.sweep_function_hook_files`が持つ。ファイルに保存するのは各用途が定める固定値だけとする。

### 定期再確認の通常回収の判定

`recheck-{session_id}.txt`は同じFunction hooks用ディレクトリに置き、PythonのStop入力を同じ会話のmodへ1回限りで渡す。
セッション限りの定期再確認taskの作成・保持・回収はmodが担い、待機対象の一覧は既存の非同期判定と`CronList`から得る。

- 書込元: `agent_toolkit/_hooks/stop.py`が各Stopの開始時に`unknown`を書く。終了判定の集約後は、既存の`background_tasks.is_pending_async_work`へtranscript・会話ID・背景task一覧・現在のセッション状態を渡す。その結果に応じて`pending`か`complete`へ更新する。読み取れないtranscript、欠落・破損と継続要求のあるStopは`unknown`を保持する。Claude Codeの会話のメインに適用し、`agent_id`を持つサブエージェントとCodexは対象外とする
- 消費元: `hooks/session_exit.ts`の既存の`turn.complete`が使用前に`consumed`へ書き換える。`complete`だけを通常回収の根拠とし、`$.tool.list()`で`CronList`の公開を確認した会話に適用する。非公開の会話では回収と通知を省き、正常な返却を保つ。適用対象では共通promptと一致する非durableの所有taskを一覧から再解決して削除し、所有taskの削除拒否・失敗を対象IDとともに通知する。追加義務付きtaskはセッション終了時の既存回収へ渡す。欠落・読取不能・不明な値は回収の許可にしない
- 初期化と寿命: `hooks/register.ts`が`session.start`とメインの`turn.start`で`consumed`へ初期化し、中断した前ターンの判定を持ち越さない。内容は各Stopで上書きし、回収の拒否・失敗後は次の有効なStopで再評価する。ファイル自体は前掲のSessionEnd・clear・期限回収に従う

### 利用上限による中断の再開予約

`rate-limit-{session_id}.txt`は同じFunction hooks用ディレクトリに置き、StopFailureのコマンドhookから`turn.complete`へ利用上限の発生を渡す。
メインのClaude Codeだけを対象とし、委譲先・`agent_id`付きの入力とCodexを除く。

- 書込元: StopFailureのコマンドhookが`error`の`rate_limit`を受けて`requested`を書く
- 消費元: `hooks/usage_limit_resume.ts`の`turn.complete`が`requested`を`consumed`へ変えて既存の再開予約へ接続する
- 初期化と寿命: 初期状態はファイル不在とし、`requested`だけを予約の根拠にする。通常のSessionEndでは保持し、`clear`と期限回収は前掲のFunction hooks用ファイルと同じ扱いとする

## 状態の継承

- `inherited_from_session_id`: 現行`session_id`の状態が不在のときに一意に特定した前身`session_id`。共通hookの起動処理が前身の全状態キーと同時に1回だけ書き込む

## plan系

- `working_plan_save_notified`: `~/.claude/plans`に残る計画バンドルの保存確認を促した事実。`agent-toolkit/agent_toolkit/_hooks/plan_save_advisor.py`が記録し、再通知の抑止に読む。セッション終了まで保持し、リセットは処理対象外とする
- `plan_mode_skill_invoked`: plan-mode起動を記録し、計画構造の自動チェックの適用判定に使う
- `current_plan_file_path`: PostToolUseが計画ファイルの編集時と公開された計画ファイル作成コマンドの出力からパスを記録し、PreToolUse(Skill)がplan-mode起動時に消去する。UserPromptSubmitのコマンドhookが名前の選択に読む
- `last_hook_session_title`: UserPromptSubmitのコマンドhookが設定した名前。現在名が空かこの値と同じ場合だけ、新しい種別・計画名への更新を許す。同じhookが通常状態へ排他下で保存する。通常状態と同じ期限と`clear`で消去する
- `session_title_kind`: PostToolUse(Skill)と、スキルの手動起動を受けたUserPromptSubmitが記録する命名用の種別（`process-wi`・`single-lane`・`add-awi`）。UserPromptSubmitのコマンドhookが名前の選択に読む。process-loopの環境印と会話IDが一致する場合は`process-wi`を優先する。通常状態と同じ寿命とする

## 振り返り・モード系

- `process_wi_skill_invoked`: process-wiスキルの起動。PostToolUse(Skill)とUserPromptSubmitが記録し、`atk agents-exit-session`の機械可読な応答を受領した時点で偽へ戻す。セッション終了まで保持する
- `autonomous_exit_invoked`: `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`が`atk agents-exit-session`の実行と機械可読な応答を記録する。
  `agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`がprocess-loopのStop判定で読む。
  同フックがバックグラウンドタスクの残存で終了要求を取り下げた時点で偽へ戻し、`atk agents-exit-session`の再実行で再び真になる。
  保持はセッション状態の有効期間中に限り、通常のスキル完了処理では再利用の対象外とする
- `stop_no_tool_turn_count`: `agent-toolkit/agent_toolkit/_hooks/busy_loop_guard.py`が、自セッションのツール呼び出しを含まないターンの連続回数を記録し、常駐ループの停止判定に読む。ツール呼び出しを観測したターン、委譲先またはバックグラウンドタスクの完了待ちのターン、停止工程の実行時に0へ戻す。セッション終了まで保持する
- `stop_observed_entry_count`: 同フックが、Stop判定の時点で観測済みのセッション記録のエントリ数を記録し、次のターンで増分だけを走査する起点に読む。セッション終了まで保持する
- `last_user_prompt_at`: `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が通常のユーザー発話（ユーザー自身が入力したスラッシュコマンドを含む）の受領時刻をPOSIX秒で記録する。
  同フックが、発話の内容を現物で確かめる手順の注記を注入するかの判定に読む。セッション終了まで保持し、リセット処理は設けない。次のいずれかに当たる発話は機械注入ターンとして記録と注入の対象から外す
  1. payloadの`source`が`user`以外である
  2. `prompt`の1行目が`<atk-auto source="periodic-recheck" kind="periodic-recheck">`か旧標識だけの行である
  3. 委譲先として起動されている
  4. `prompt`が`<task-notification`か旧形式の`<cross-session-message`で始まる
  5. `prompt`の1行目が新旧の自動挿入要素の開始タグを含む

## 現行の作業契約の入力位置

- `work_contract_input_sequence`: `_hooks/work_contract.py`がUserPromptSubmitのメインの実ユーザー入力だけで排他下に加算するセッション内の連番。同じモジュールが記録内の処理済み連番と最新の連番を比べ、UserPromptSubmitとSessionStartのcompactで記録の所在と未処理の入力区間を通知する。機械側の保存対象は連番に限り、入力本文・分類・達成状態はエージェントの記録で扱う。通常の状態ファイルと同じ寿命で保持し、圧縮・再開でも連番を継承する。

## 確認・操作スキル系

- `user_confirmation_skill_pending`: `agent-toolkit:user-confirmation-and-report`の内容が文脈に無い状態（真偽値）。
  `agent-toolkit/agent_toolkit/_hooks/rules_context.py`がSessionStartの`source`が`startup`・`clear`・`compact`で委譲先でない場合に真にし、`resume`と`fork`では変えない。
  `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が実ユーザー発話の受領時に読み、真なら同スキルの起動を促す注記を返す（機械注入ターンでは読まず、変えない）。
  PostToolUse(Skill)が同スキルの起動で、UserPromptSubmitがユーザーによる同スキルのスラッシュコマンド起動で偽へ戻す。Skillの起動を観測できないCodexでは、注記を返した時点で偽へ戻す。
  寿命はセッション状態ファイルと同じとする
- `operation_skill_delivered_agents`: 資料識別子から、その資料を取得した主体の一覧への対応。既存キーを継承し、宣言型配送の識別子は`read-trigger:<絶対パス>#sha256:<本文hash>`、節は`#heading:<節指定>`、分割取得の範囲は`#range:<開始>:<終了>`を付ける。本文とhashは同じ読取時点の内容を使う。主体はhook payloadの`agent_id`、持たない会話は`main`とする。`selected-read-target:<JSON>`は選択済みのpathとheading、`modified-agent-document:<絶対パス>`は変更した規範を保持する。`review-reference:<絶対パス>`はprepare結果の資料、`read-trigger-role:<絶対パス>`は受領した役割を保持する。これらの選択情報は圧縮時も保持し、本文取得状態だけをリセットする。`startup-input-target:<JSON>`は起動時に受け取った入力資料のpath・heading・selectors（入力名の配列）・本文hashを保持し、圧縮後の再配送へ使う。`review-output:<絶対パス>`はprepareの未取得の保存JSON、`review-output-parsed:<資料識別子>`は末尾まで取得して解析した版を示す。JSONから実在する参照資料を登録した後は未取得の保存JSONを解除する。
  委譲先自身の引き継ぎ記録は、`AGENT_TOOLKIT_HANDOFF_PATH`が示す絶対パス1件だけを本文編集時のwriting-standards取得要求から除く。この除外で取得済みの状態は付けない。同じディレクトリの別ファイル、環境値が無い入力と記録先以外を含む複数ファイルの編集は、通常の取得状態で判定する。
  `resolved-project-skill:<絶対パス>`は実際の取得入力から確定したproject skillの宣言元を保持する。起動時の取得記録は入力metadataと実際に配送された同版本文が一致する場合に付け、metadataだけの抜粋を取得済みとは数えない。入力識別と宣言元は圧縮時にも保持する。
  `startup-context-observed`は正式な起動入力の取得先と役割を登録した主体を保持し、圧縮後に履歴の本文を再計上することを防ぐ。`startup-context-reset`は最初の操作より前のclear・compactも記録する。同じ記録へ本文取得の前提が戻ることを防ぎ、実体指定からの再取得へつなぐ。
- `termination_evidence`: `agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が終了工程の証拠を作業単位で保持する。中身は作業ごとの成果作業の種類、completion-reportの起動証拠、呼び出し、判断記録、遅れて返る応答を元の作業へ対応付けるための未完了の呼び出しと、判断の根拠となる人間の入力である。
  PreToolUse・PostToolUse・UserPromptSubmitが呼び出しと人間の入力を更新し、Stopの`termination_order_advisor`が成果作業と起動証拠の対応を読む。報告本文、完了の自己申告と起動済みを示す独自のフラグは保持の対象外とする。
  解決済みの呼び出しと入力は更新のたびに除き、版や形式が異なる値は破棄してStop判定ログへ診断を残す。
  寿命と継承はセッション状態ファイルと同じとし、別会話から継承した値は`session_id`の一致を確かめてから使う
  - 成果作業と起動証拠: 成功した対象スキルの開始、リポジトリへの自編集、計画の保存、WIの保存、commit・pushを`deliverables`へ記録する。成功したcompletion-reportのSkill起動（Claude Code）と、結果が本文を返した`SKILL.md`の読取（Codex）は`completion_report`へ記録する。起動証拠を得た作業へ新しい人間の入力の後に成果作業が届いた場合は新しい作業を始める
  - 待機の許可: 成果作業が待つ非同期対象の生存中にStopが待機を許可する条件は`claude-hooks-stop.md`「終了工程の証拠」が定める。本キーは作業の開始位置（UserPromptSubmitが記録したtranscriptの大きさ）を保持し、Stopがその判定に読む
  - 成果作業の対象: 観測したリポジトリのrootを`repositories`へ保持する。直接実行のGit操作は`-C`などの指定を含めて対象を取得し、シェルの移動や前置代入で対象を取得できない操作は`null`を保持する。対象が単一リポジトリと確定できる作業だけ、プロジェクト規範による省略を受理する
  - 委譲先へのwait判断: `async_targets`と所有者を確認し、観測義務とは別に有効なCLI待機所有権・対象登録を共通の読取処理で確認する。`pending_observation`が偽でも待機が有効なら保持し、両方が終わると不足判定へ戻る
  - 自編集: 成功したEdit・Write・`apply_patch`と、既存の抽出で静的に確定したBash書込先を、作業ごとの`edits`へリポジトリと相対パスで記録する。Claude Codeの協調モードのメインでは、Stopがその集合だけを終了時のGit状態と比べて公開範囲が未確定の編集を遮断の通知へ添え、他主体の差分を取り込まない
  - 公開範囲: メインが原入力または適用済み認可から判断した範囲を`publish_scope`へ記録する。記録手順は`agent-toolkit:user-confirmation-and-report`の`references/publish-scope.md`が定める
  - プロジェクト規範による省略: メインが`omit-completion`で指定した規範の絶対パス、記録処理がそのパスから読んだ全文、対象リポジトリと人間入力の識別子を、作業ごとの`omit_completion`へ保持する。`input_id`の省略時は現在の会話の直近の人間入力へ対応付ける。記録時は観測した成果作業の単一リポジトリ、規範の所在、現在のメイン・会話・入力の対応を確かめ、明示した`quote`が規範全文と一致するかも確かめる。Stopでも規範の実体と入力の対応を確かめる。有効な省略を持つ作業だけ起動不足を免除する。次の判断で除き、新しい人間入力後の成果作業は新しい作業として省略を引き継がない。規範の変更・消失は免除を無効にする。手順は`agent-toolkit:completion-report`の`references/termination-evidence.md`が定める
  - 原入力と再入: `last_human_input`は最後の人間入力を保持し、自動通知で再開しても元の依頼への帰属を保つ。`--context`は現在の会話の`session_id`と作業の`work_id`を人間入力の有無によらず返し、その入力を観測した場合だけ`input_id`と`quote`を添え、観測していなければ両者を`null`とする。証拠が無いか別会話・旧版の値である場合、現在のメインとして解決できない場合と、作業も人間入力も無い場合は取得を拒否する。取得は判断の証拠条件を緩めず、人間の原入力・実際の失敗・所有する委譲先の待機の検証はactionごとの記録処理が行う。寿命と継承は本キーと同じで、公開範囲・起動証拠と現在のGit状態から不要な判定を除く
  - 終了時の点検通知: `stop_review_notice`は作業ごとに通知した人間入力の識別子を保持する。人間入力が無い場合は作業開始の参照を使う。Stopの`termination_order_advisor`が`claim_stop_review`で排他下に記録し、同じ作業・入力への再通知を抑える。通知した事実だけを示す記録であり、用途は同じ作業・入力への再通知の抑止に限る。新しい入力では再び一度通知し、作業の除去とともに消える

## 通知反復系

- `persistent_warning_fingerprints`: `_atk/outcome.py`が共通起動の持続状態とhookの引数不整合の警告本文の指紋を記録する。同じ会話で初めて報告する内容だけを出力する。CLIの識別子はowner・Claude Code・Codexの順、hookは入力の`session_id`を使う。委譲先の掃引警告は記録と出力を省く。寿命はセッション状態ファイルと同じとする
- `warn_notice_counts`: `warn`区分の通知の、`<hook_id>|<原因識別子>`ごとの累積件数。通知の整形処理が記録し、2件目以降の通知へ反復していることと件数を載せるために読む。診断処理が要旨を渡した通知では2件目以降の本文を要旨と件数へ短縮する。セッション終了まで保持し、リセットは処理対象外とする

## agents_server連携系

- `agents_server_cwd_by_session`: `session_id`ごとの絶対`cwd`。`send_message`と`kill`の事前判定および各ツールのPostToolUse状態更新に使う。`show`の応答が返す稼働中の子sessionの識別子と`cwd`の対も記録し、委譲元がその子sessionへ追送と打ち切りを発行できるようにする
- `agents_server_sessions`: `session_id`ごとの公開状態と内部状態。寿命はセッション状態ファイルと同じとする
  - 記録する値: 公開状態は`session_id`・`status`・`kill_requested`・`pending_observation`・`owner_agent_id`・`model_type`・`error`・`agent_message`、内部状態は`turn_id`とする。`pending_observation`は観測を試みていない作業の有無、`owner_agent_id`はその作業を発生させた主体を示し、`error`と`agent_message`は終端時の値とする
  - `model_type`の解決: 起動入力の`model_type`を優先し、省略時は`start`の`task`では共通パーサーが読む役割文書のfrontmatter、他の`mode`では`mode`ごとの省略時の種別とする。ファイル名の対応表は旧文書の読取互換だけに使う。統合前の旧名で記録された開始は対応する`mode`として扱う
  - 生成と更新: `start`の成功応答で生成し、`send_message`・`kill`・`wait`の応答境界と、Bash経由の`atk agents wait`実行時に更新する
  - `pending_observation`を真にする契機: 各開始操作の成功応答と、`delivery`が`reply_started`または`reply_ambiguous`の`send_message`の応答。`steered`は実行中のturnへ追加指示を配送するだけで`turn_seq`を変えないため対象外とする。呼出主体をhook payloadの`agent_id`（無いメイン会話は`main`）から`owner_agent_id`へ記録する。`transcript_path`で呼出主体を判別しない（`claude-hooks.md`「hookスクリプトの基本プロトコル」）
  - `pending_observation`を偽にする契機: `kill`の成功応答、Bash経由の`atk agents wait`完了時、Stop時に直前の応答の`待機中:`表明がそのsessionを指す場合。CLIの`atk agents wait`は入力sessionを取らず、呼出主体が所有する既存の記録を持つ全sessionを観測済みにする。偽は現時点で未観測の作業が無いことを示すため、新しい作業を配送すれば再び真になる。`owner_agent_id`は次の作業発生まで保持する
  - 利用先: Stop判定で、`pending_observation`が真で`owner_agent_id`がStopの呼出主体と一致する記録だけを警告へ使う。CLI待機中はルートセッションが所有する待機所有権の生存をStopフックが確認し、未観測警告の対象から除く。終了工程の委譲先wait判断も同じ読取を使い、観測義務と有効な待機を区別する。`status`が`running`の記録の件数は待機の遮断の入力にしない
  - 除去: `stop`の成功応答でその`session_id`のエントリーを除く。`stop`は実行中turnを持つsessionと非終端のsessionを拒否するため、未終端のsessionの記録は失われない
  - 持たない状態: 結果を回収済みであることを示す状態と、thread IDをハッシュ化した状態ファイル

## バックグラウンドタスク系

- `background_task_ids`: PostToolUseが、自セッションのツール呼び出しの応答から得たタスクIDを重複なく記録する。この記録をバックグラウンドタスクの所有記録と呼ぶ。バックグラウンドタスクは、BashやAgentを背景で動かした非同期の処理を指す。記録の契機は次の4つで、所有の根拠は自身の呼び出しが識別子を返したことである。
  1. Bashの`run_in_background`指定の応答（成功・失敗とも）
  2. 実行上限またはユーザーの操作で背景へ移ったBashの応答が返す構造化`backgroundTaskId`
  3. MCPツールの呼び出しが背景へ移ったときのホストの移行通知。テキストの先頭から始まる`MCP tool "<ツール名>" is still running after <秒数>s. It was moved to the background as task <ID>`の形だけを記録し、前景の出力本文の途中に現れる同じ文言は除く
  4. `Agent`・`Task`の背景起動の応答（`status`が`async_launched`）が返した`agentId`

  PreToolUse(TaskStop)が停止対象が自セッションのバックグラウンドタスクかの判定に読む。PreToolUse(Bash)も未完了の出力ファイルの読取を警告する判定の前に読み、記録が1件以上ある場合だけtranscriptを読む。出力パスは状態へ記録せず、`background_tasks.py`と同じ起動集合と完了集合から選んだ未完了の背景Bashについて、起動の`tool_result`本文から得る。セッション終了まで保持し、リセットしない
- `queued_notification_notified_ids`: Stopの`queued_notification_advisor.py`が、最上位transcriptのキューに残る未配送の完了通知について案内した通知の識別子を重複なく記録する。
  識別子は`<task-id>`、無ければ`<tool-use-id>`、どちらも無ければ通知本文とする。Agent・Taskの起動記録に対応する通知では返却メッセージの利用を、それ以外では出力ファイルの読取を案内する。他のStop判定のblockがある場合だけ、同じ通知への警告を1回に限る判定に読む（繰り返すと、ツールを呼ばないターンの継続を反復させる）。blockが無い場合は案内済みかによらず現在の未配送通知を使い、ホストの配送による次ターンへ進む。警告回数と本キーの更新は実際に警告する場合に限る。セッション終了まで保持し、リセット処理は設けない
- `task_stop_blocked_at`: PreToolUse(TaskStop)が遮断した時刻のPOSIX秒。同フックが再実行許可窓の判定に読む。セッション終了まで保持し、リセットしない
- `stall_detection_completed_at_by_task`: `record_stall_detection.py`が停滞検知を完了した停止対象の完全なタスクIDをキー、完了時刻のPOSIX秒を値として記録する。PreToolUse(TaskStop)は5分以内の一致する対象だけを停止可能と判定し、期限切れ要素を除く。成功したPostToolUse(TaskStop)が一致要素を消費し、停止の失敗時は同じ5分窓で再試行できるよう保持する

## UWI系

- `uwi_answered_by_repo`: PostToolUseが記録する回答済みUWIファイル名。値は`{エージェント識別子: {対象リポジトリID: ファイル名一覧}}`の2段辞書とし、`agent_id`を持たないメイン会話は`main`をキーとする。メインとサブエージェントのフック呼び出しは同一`session_id`で届くため、エージェント識別子で分けないと一方が回答差分を消費して他方へ通知が届かない。
  cwdが空・保存先未解決・後述の指紋が前回と同一・リポジトリID未解決・走査不完全のいずれかでは更新しない。前回の一覧にない回答済みファイル名の通知判定に使い、セッション終了まで保持し、リセットせず一覧の更新だけで状態を進める
- `uwi_fingerprint_by_repo`: PostToolUseが記録するactive状態ディレクトリ（`inbox`・`processing`）の内容変化指紋。値は`{エージェント識別子: {作業ディレクトリ: 指紋文字列}}`の2段辞書とし、前回と同一なら走査と回答差分の検出を省く。回答済みファイル名一覧と同じ更新で記録し、セッション終了まで保持する

上記2つのキーは、現行のキーが無い場合だけ改名前のキー`tbd_answered_by_repo`・`tbd_fingerprint_by_repo`を読み取って引き継ぎ、書き込みは常に現行のキーへ行う（配布をまたいで稼働するセッションが改名前のキーで基準値を持つため）。

## Antigravityのイベント間状態

Antigravityの6イベントアダプターは共通セッション状態へ次の値を保存する。
更新と一括消費は`update_state`の排他区間で行い、既存のTTLスイープで回収する。

| キー | 型 | 書き込みと消費 |
| --- | --- | --- |
| `antigravity_pending_contexts` | `list[str]` | `SessionStart`、`PreToolUse`、`PostToolUse`が延期通知を蓄積し、`PostInvocation`で一括消費する。初回の`PreInvocation`も起動通知を消費する |
| `antigravity_last_user_step` | `int` | `PreInvocation`が処理済みの`USER_INPUT`の`step_index`を記録し、同じ発話の再処理を防ぐ |
| `antigravity_stop_continuing` | `bool` | 直前の`Stop`が`continue`を要求した事実を保存し、共通入力の`stop_hook_active`へ正規化する |
| `agy_cwd` | `str` | `SessionStart`が正規化したworkspaceのcwdを保存し、`session_records`が記録の解決に使う |
| `agy_transcript_path` | `str` | `SessionStart`が`transcriptPath`を保存し、`session_records`が記録の読取に使う |
| `antigravity_subagent_ids` | `list[str]` | 親の`PostToolUse`が`invoke_subagent`の返却から作成済みの`conversationId`を登録する。件数の一致した`manage_subagents`のactive一覧、または`Stop`の`fullyIdle=true`で終端を観測する |

所有するsubagentの開始は共通の`background_task_ids`にも登録する。
`background_task_ids`は起動の所有履歴としてTTLまで保持し、終端観測では削除しない。
active一覧と`fullyIdle`による更新は`antigravity_subagent_ids`へ反映する。
Antigravityに存在しない`SubagentStart`・`SubagentStop`の直接購読は行わない。
