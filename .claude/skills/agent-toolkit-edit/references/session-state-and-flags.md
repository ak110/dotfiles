# セッション状態ファイルとフラグ

本書はhook間の共有状態ファイルの設計、生存期間、排他制御、各フラグの記録元・参照先を定める。
状態ファイルのパスは`{state_dir}/hook-sessions/claude-agent-toolkit-{session_id}.json`とする。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:report-completion/references/claude-hooks-stop.md#「終了工程の証拠」` | 判断 | `section:agent-toolkit:*/skills/report-completion/references/claude-hooks-stop.md#「終了工程の証拠」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:user-confirmation-and-report/references/publish-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/publish-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:report-completion/references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/termination-evidence.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md#「hookスクリプトの基本プロトコル」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks.md#「hookスクリプトの基本プロトコル」` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state.md`の「run-skillの再開入力」 | 判断 | `section:*/.claude/skills/agent-toolkit-edit/references/agents-server-shared-state.md#「run-skillの再開入力」` |

## 資料の選択

読み書きする状態、更新主体、消費側との利用関係から必要な共通節と主題節を選ぶ。
複数の状態を扱う変更では、その和と必要な前提・例外を読む。
ファイル名だけで範囲を限定せず、選択した節の前提・例外が別の主題にある場合も、その節を加える。

## セッション状態資料の選択

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| セッション状態や内部フラグを変更・調査する前 | `session-state-and-flags.md`の「状態ファイルの設計」「状態の継承」。設計節配下の「並行書き込みの排他制御」も含む | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「状態ファイルの設計」「状態の継承」` |
| 活動・WI投入作業の記録を変更・調査する前 | `session-state-and-flags.md`の「セッションの活動記録」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「セッションの活動記録」` |
| managed-tempの状態を変更・調査する前 | `session-state-and-flags.md`の「managed-tempの状態」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「managed-tempの状態」` |
| 完了報告からの状態解析を変更・調査する前 | `session-state-and-flags.md`の「完了報告からの状態読み取り」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「完了報告からの状態読み取り」` |
| Function hooksの終了要求、定期再確認や利用上限の再開予約を変更・調査する前 | `session-state-and-flags.md`の「Function hooksの終了要求」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「Function hooksの終了要求」` |
| 作業契約の状態を変更・調査する前 | `session-state-and-flags.md`の「作業契約系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「作業契約系」` |
| plan・セッション名の状態を変更・調査する前 | `session-state-and-flags.md`の「plan系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「plan系」` |
| 振り返り・モード・無進捗停止の状態を変更・調査する前 | `session-state-and-flags.md`の「振り返りとモードの状態」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「振り返りとモードの状態」` |
| 進捗点検の状態を変更・調査する前 | `session-state-and-flags.md`の「進捗の点検系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「進捗の点検系」` |
| 確認・操作スキルの起動、資料取得証拠、読込の選択・案内や終了工程の証拠を変更・調査する前 | `session-state-and-flags.md`の「確認・操作スキル系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「確認・操作スキル系」` |
| 通知の反復抑止・配送状態を変更・調査する前 | `session-state-and-flags.md`の「通知反復系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「通知反復系」` |
| agents_serverの呼出・所有・待機の観測状態を変更・調査する前 | `session-state-and-flags.md`の「agents_server連携系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「agents_server連携系」` |
| バックグラウンドタスクの所有・通知・停止の状態を変更・調査する前 | `session-state-and-flags.md`の「バックグラウンドタスク系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「バックグラウンドタスク系」` |
| UWI回答の検出状態を変更・調査する前 | `session-state-and-flags.md`の「UWI系」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「UWI系」` |
| Antigravityのイベント間状態を変更・調査する前 | `session-state-and-flags.md`の「Antigravityのイベント間状態」 | 判断 | `section:*/skills/agent-toolkit-edit/references/session-state-and-flags.md#「Antigravityのイベント間状態」` |

## 状態ファイルの設計

Claude Code・Codexのhookは呼び出しごとに別プロセスで起動するため、セッション単位の状態ファイルで情報を共有する。

- パス規則: 本書の冒頭に示したパスを、`_common/state_paths.py`の`state_dir()`と`payload["session_id"]`から組み立てる
- 置き場所の選定: 生存期間と共有範囲で選ぶ。セッション再開・ホスト再起動後に使う記録と排他ロックは同じ状態ディレクトリへ置く。一時ディレクトリへ置けるのは、消失しても判定情報を失わない診断ログや再生成可能なキャッシュに限る
- 旧位置からの移行: 新位置に状態ファイルが無い場合だけ、排他ロック下で同じ`session_id`の旧位置（`tempfile.gettempdir()`配下）のJSONを移す。読み取り・更新・期限切れ回収・前身状態の継承には新位置を使う
- データ形式: 単一JSONオブジェクト。新規フラグのキーはsnake_case、既存名は読取側の契約として維持する
- 読み書きの契機: スキル呼出・バックグラウンドタスク起動などのPostToolUseイベントで書き込み、PreToolUse・UserPromptSubmit・Stopの判定時に読み取る
- 破損・不在時の扱い: 空の辞書として扱い、安全側に判定する
- `session_id`の切り替え: 現行の状態ファイルが存在しない場合に限り、transcriptを先頭から走査し、状態ファイルを持つ別の`session_id`が一意に特定できるなら、その全キーを継承して継承元を記録する。現行の状態ファイルがある通常時は、存在確認だけで終える
- フックイベント間の同期: 主体・文脈・受信版と、実際に発生した出来事を区別して必要な記録だけを共有する。資料取得では既知範囲のdigestと配送後の本文を対応付け、実際の受信だけを根拠にする。保存内容は本文を除く識別情報に限定する
- フラグ仕様の文書化: 新規フラグや同じ状態ファイルを共有する個人フックのフラグも含め、用途、書き込み元、読み取り元、寿命、リセット処理を後掲の各節に記す（努力目標）
- 状態JSONの削除契機: 状態ファイルを削除するのは、期限切れの定期回収と、終了理由が`clear`のSessionEndだけとする。SessionEndは`--continue`、`--resume`、`/resume`で同一の`session_id`に復帰する場合にも発火するため、他の理由で削除すると再開後の記録が失われる。期限切れ回収の対象は最終更新から一定期間が経過した通常の状態ファイルとし、`clear`時は排他ロック下で、対象セッションの新旧両方の配置場所にある状態ファイルを削除する
- 出来事と更新の対応: 時刻や消費済みの記録を持つ状態は、記録が証明する出来事（通知の採用、資料の取得など）と更新を許す出来事を対応付け、予約（生成・試算）と確定（採用・配送）、周期の上限、最小間隔の抑止、活動後のデバウンスを区別する。別種のイベント列を反復しても要求した出来事へ到達するかは、生成・採用・出力を接続した試験で確かめる
- サブエージェント起動の判定: `tool_name in ("Agent", "Task")`をSSOTとする。子の状態は委譲元へ自動伝播しないため、親が必要な値は完了報告の構造化フィールドから厳密に解析する

### 並行書き込みの排他制御

Claude Codeはツールの並列呼び出しでhookを同時に発火させるため、同じ状態ファイルへの書き込みが競合する。

- 状態の更新処理は、排他ロックを備えた`update_state`関数へ集約し、セッション命名用の種別や自動設定されたセッション名も同一のAPI経由で保存する
- 通常のロックファイルは`{state_dir}/hook-sessions/.state.lock`の固定1個とし、`TMPDIR`が異なるプロセスも同じロックで排他する
- ロックは状態の読み取り・変更・書き込みの短い区間だけ保持し、時間のかかるtranscriptの読み取りや継承候補の準備はロックの外で行う
- 前身状態を継承する際は、継承候補を準備した後に同一の共有ロック下で現行状態が不在であることを再確認し、二重継承を防ぐ
- `clear`による削除や期限切れ回収の処理は、同一の共有ロック下でJSONファイルを削除し、固定ロックファイル自体は保持する
- 旧一時ディレクトリのロックは、旧JSONファイルの移行処理に必要な範囲に限定して使用する
- 並行書き込みに関する回帰テストを継続して維持する

## セッションの活動記録

`_common/session_activity.py`は、OSアカウントの状態ディレクトリ配下にある`session-activity/{session_id}.json`へ、会話ごとの最新状態を保存する。
状態ディレクトリの取得元は`agent-toolkit/agent_toolkit/_common/state_paths.py`の`state_dir()`である。
`hook.py`は既存イベントの処理で活動記録を更新し、`submission_activity`はprocess-loopの開始前判定へ、対象リポジトリへ進行中のWI投入作業の活動状況を渡す。

- 保存する項目は、バージョン番号、会話ID、実行系、PIDと起動時刻、会話内の各エージェントの活動状態、親会話ID、セッション記録のパス、バックグラウンドタスクの有無、および更新時刻である
- SessionStart、UserPromptSubmit、各種ツールイベント、SubagentStartの発生時に活動を記録し、StopやSubagentStopでは実際のhookレスポンスが続行を求めるかを反映する
- 応答待ち（アイドル）状態とみなして活動から除外する条件には、セッション記録上のターン終了またはホスト側で登録されたidle状態であり、かつ稼働中の子エージェントが存在しないことを用いる
- SessionEndの発火時に活動JSONを削除する。記録されたPIDと起動時刻が生存プロセスと一致しなければ、取得側で失効として扱う
- 更新および削除処理は固定の`activity.lock`による排他制御下で実行し、会話ごとの過去履歴や終了済みの子エージェントの項目は保持せずに削除する
- 読み取れない活動記録は安全側に倒して活動候補として返す。関連する会話を特定できない生存プロセスは、どの作業にも属さないものとして集計から除く
- WI投入作業の属性情報は、`session-activity/submissions/{session_id}.json`へ会話ごとに保存する。保存項目は、バージョン番号、会話ID、作業種別（`add-awi-by-user`）、正規化された投入先URLの一覧、作業開始時刻（`started_at`。最初の`begin`で付け、投入先の置換では保つ）、更新時刻である。このファイルは`atk wi submission begin`が作成・更新し、`atk wi submission end`が照合の成立後か`--abort`で削除する。正常な`end`は報告用要約を`session-activity/submission-reports/{session_id}.json`へ保存し、Stop hookが最終応答との照合後に削除する。hookのSessionEndでは削除せず、セッション再開時にその属性を再び有効化できるようにする
- 開始前判定では、投入先がprocess-loopの対象リポジトリ群と重複している属性を持つ会話、およびその子孫の活動のみを待機理由とし、属性を読み取れない会話や属性を持たない会話は除外する
- 対象外とする会話とその子孫の判定には`_common/session_parents.py`による親子関係の解決結果を用い、process-loopの共通環境変数などの識別印だけで別ループの会話まで誤って除外しないようにする

## managed-tempの状態

- 各セッションのmanaged-tempディレクトリ名は、一時ルート直下の`session-<sha256(session_id)の16進表記の先頭16桁>`とし、すべての導出で共通の関数を使って決める。新しい領域の状態は、一時ルート直下に実体として存在するディレクトリのみで管理する
- 旧バージョンが作成した状態ルート（`<state_dir>/managed-temp`）の`.sessions/<sha256(session_id)>`が一時ルート直下の実在ディレクトリ（非シンボリックリンク）を指す場合は、旧パスを同一会話の領域として再利用し、参照処理は読み取りのみに限定する。旧マーカー（`.agent-toolkit-managed-temp.json`）、旧登録簿、`.sessions`参照、cleanupロックの名前変更や一括削除は行わない。定期スイープが回収条件を確かめ、対象ごとのロックを非ブロッキングで取得できたものだけを個別に回収する
- 7日周期のスイープ処理における次回の確認時刻は、状態ルートの`.sweep/schedule`内に、一時ルート直下のディレクトリ名をキーとする単一のテーブル形式で記録する

## 完了報告からの状態読み取り

完了報告から状態を機械的に読む対象は、末尾の連続した記録行ブロックか最終行に限る。本文中の引用による誤検出を防ぐためである。
記録行は既知のキー（例: `invoked_subagents`、`codex_unavailable`）の許可リストで判定する。末尾付近の「`キー:`」形式の散文を誤検出するため、英字キー全般に一致する正規表現は使わない。

## Function hooksの終了要求

Claude Codeが会話イベントのハンドラーを登録するために読み込むプラグインモジュールを、Function hooks module（以下「mod」）と呼ぶ。このmodと、Python側のCLIおよびStop hookは、同じセッションIDの読み込み目印（マーカー）と終了要求を共有する。配置場所は`.claude/agent-toolkit-function-hooks/`ディレクトリとする。親ディレクトリは絶対パス指定の`CLAUDE_CONFIG_DIR`、未設定時は`HOME`、さらに未設定時は`USERPROFILE`とし、Python側の一時ディレクトリ解決ロジックは使用しない。このディレクトリ内に`marker-{session_id}.txt`と`request-{session_id}.txt`を配置する。セッションIDとして受理するのは英数字、アンダースコア、ハイフンのみとする。

`session.start`の発火時に読み込み目印へ`ready`を書き込み、終了要求ファイルを`consumed`に初期化する。Python側は、目印が`ready`であり、かつファイルの更新時刻が現在のClaude Codeプロセス起動時刻より後である場合に限り、終了要求ファイルへ`requested`を書き込んで`exit_requested`を返す。`turn.complete`はメインエージェントのターンでのみこの値を読み取り、値を`consumed`へ書き換えて`/exit`をキューへ投入する。目印がない場合やCodexでは、対話CLIプロセスを従来どおり終了させる。

`/exit`はセッション限りの未完了タスクが残ると確認画面を表示するため、`turn.complete`は`/exit`を投入する直前に、`durable`が真ではないcronタスクを削除する。タスク一覧の取得と削除は`$.tool.check`が`allow`の場合だけ行い、削除に失敗しても`/exit`は投入する。
process-loopによって起動された会話の最上位Stopでは、終了要求が`requested`であり、かつStop入力の`background_tasks`に有効な通常タスク（非`teammate`タスク）が残っているかどうかを判定する。この条件に該当する場合、`agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`は終了要求を`consumed`に戻して終了をブロックする。Stop hookは同じターンの`turn.complete`より先に動くため、取り下げた終了要求で`/exit`が実行されることはない。

Stop hookによる無進捗停止ガード（`busy_loop_guard.py`）からの終了要求は、Stopの全判定が完了した後に共通ハンドラー（`stop.py`）から発行される。終了要求が`requested`か特定PIDへの終了要求として成立した場合、そのStop hookの応答は停止の通知だけになる。このため`autonomous_exit_invoked`が偽のままでも、無進捗停止の終了要求は`turn.complete`で消費される。`autonomous_exit_invoked`が記録するのは`atk agents-exit-session`の実行履歴だけで、Stop内部で発行した終了要求はこのフラグと独立に終了要求ファイルの状態で管理する。

通常のSessionEndでは、現行セッションの読み込み目印、終了要求、後述する定期再確認の判定結果、および利用上限の記録ファイルを保持したまま、保持期限を過ぎた他セッションの古いファイルを回収する。終了理由が`clear`の場合は現行セッションの4ファイルも削除する。保持期限の管理とファイル回収処理は`agents_exit_session.sweep_function_hook_files`が担当する。各ファイルに保存する文字列は、用途ごとに定めた固定値に限る。

### 定期再確認の通常回収の判定

`recheck-{session_id}.txt`は同一のFunction hooks用ディレクトリに配置し、Python側のStop入力を同一セッション内のmodへ1回限りで受け渡すために使用する。
セッション限りの定期再確認タスクの作成、維持、回収はmodが担当し、待機対象タスクの一覧は既存の非同期判定ロジックおよび`CronList`から取得する。

- 書き込み元: `agent_toolkit/_hooks/stop.py`が各Stopの開始時に`unknown`を書き込む。終了判定の集約後、既存の`background_tasks.is_pending_async_work`へtranscript、会話ID、バックグラウンドタスク一覧、および現在のセッション状態を渡す。判定結果に応じて`pending`か`complete`へ更新する。読み取れないtranscript、データの欠落や破損がある場合、あるいはセッション継続要求を伴うStopでは`unknown`を維持する。この処理はClaude Code会話のメインエージェントにのみ適用し、`agent_id`を持つサブエージェントやCodexは対象外とする
- 消費元: `hooks/session_exit.ts`にある既存の`turn.complete`が、判定を利用する直前に`consumed`へ書き換える。`complete`の値のみを通常回収の根拠として扱い、`$.tool.list()`によって`CronList`の公開が確認できた会話に適用する。非公開の会話では回収と通知を省き、正常に復帰する。適用対象の環境では、共通のプロンプトと一致する非durableな所有タスクを一覧から再度特定して削除し、所有タスクの削除が拒否または失敗した場合は対象タスクIDとともに通知する。追加のクリーンアップ義務が付随するタスクは、セッション終了時の既存の回収処理へ引き渡す。欠落、読み取り不能、不明な値の場合はタスク回収を許可しない
- 初期化と生存期間: `hooks/register.ts`が`session.start`およびメインエージェントの`turn.start`で`consumed`に初期化し、中断された前ターンの判定結果を持ち越さないようにする。ファイルの内容は各Stopの実行ごとに上書きされ、回収の拒否や失敗が発生した後は、次の有効なStopにおいて再評価する。ファイル自体の寿命は、前述のSessionEnd、`clear`、期限切れ回収の規定に従う

### 利用上限による中断の再開予約

`rate-limit-{session_id}.txt`は同一のFunction hooks用ディレクトリに配置し、StopFailureコマンドhookから`turn.complete`へ利用上限（レート制限）の発生を通知するために使用する。
この処理はメインエージェントのClaude Codeのみを対象とし、委譲先、`agent_id`が付与された入力、およびCodexは除外する。

- 書き込み元: StopFailureコマンドhookが、`error`として`rate_limit`を受け取った際に`requested`を書き込む
- 消費元: 通常のClaude会話は`hooks/usage_limit_resume.ts`の`turn.complete`が`requested`を`consumed`に変え、既存の再開予約処理へ接続する。process-loopの同じ会話IDに対応する管理印、およびrun-skillの実行IDがある会話では起動側が再開を担当するため、modによる予約を省く。process-loopは`requested`を読んで旧CLIの終了を確認し、要求ファイルを削除して同じIDの接続を選び直す。run-skillはCLIのstream-jsonから上限を観測する
- 初期化と生存期間: 初期状態ではファイルを作成せず、`requested`の記録だけを予約実行の根拠とする。通常のSessionEndではファイルを保持し、`clear`や期限切れ回収時の扱いは前述のFunction hooks用ファイルと同様とする

## 状態の継承

- `inherited_from_session_id`: 現行の`session_id`に対応する状態が存在しない場合に、一意に特定された前身の`session_id`を記録する。共通hookの起動処理が、前身の全状態キーの引き継ぎと同時に1回だけ書き込みを行う

## 作業契約系

run-skillが保持する元の実行入力・会話・時間予算は、[共有状態資料のrun-skillの再開入力](agents-server-shared-state.md#run-skillの再開入力)に従う。
通常のhook状態JSONへ複製しない。

- `whole_contract_notified`: 委譲先の主体ごとに、全体作業契約の再読案内を最終出力へ採用した内容ハッシュの一覧を保持する。PreToolUseと圧縮後のSessionStartが、起動時の正式な作業契約入力と既存の資料読了記録から未読版を判定する。本文は配送せず、同じ版は1回だけ非遮断で案内する。実際の全文読取後は案内を抑止し、compact時はこの通知記録を削除して所在を再案内する。セッション状態と同じ寿命で保持する。
- `work_contract_input_sequence`: UserPromptSubmitが実ユーザー入力ごとに進める整数。初期値は0で、機械注入の間は値を保つ。PreToolUseが未作成通知の入力区切りとして参照する。セッション状態と同じ寿命で保持する。
- `work_contract_notified_sequence`: PreToolUseが未作成のメインへ通知した入力連番。排他ロック下で比較・更新し、同じ入力では1回だけ通知する。SessionStartの`compact`時に削除して、圧縮後の最初の操作で再通知する。他はセッション状態と同じ寿命で保持する。

## plan系

- `plan_mode_skill_invoked`: write-planスキルの起動を記録し、計画ファイルの構造に対する自動チェックを適用するかどうかの判定に使用する
- `current_plan_file_path`: PostToolUseが、計画ファイルの編集時や公開された計画作成コマンドの出力からファイルパスを記録する。PreToolUse(Skill)がwrite-plan起動時にこの値を消去する。UserPromptSubmitのコマンドhookがセッション名を選択する際にこのパスを参照する
- `last_hook_session_title`: UserPromptSubmitのコマンドhookが設定したセッション名。現在のセッション名が空であるか、この値と一致している場合に限り、新しい種別や計画名への更新を許可する。同hookが通常状態ファイルへ排他ロック下で保存する。通常状態ファイルと同様の保持期限および`clear`のタイミングで消去される
- `session_title_kind`: PostToolUse(Skill)、およびスキルの手動起動を受け取ったUserPromptSubmitが記録する命名用の種別（`process-wi`、`single-lane`、`all-repos`、`add-awi`）。UserPromptSubmitのコマンドhookがセッション名を選択する際に参照する。種別が`all-repos`の場合と、process-loopの環境識別印と会話IDが一致している場合（記録された種別を問わない）は、計画名を連結しない統括名`process-wi all-repos`を使う。生存期間は通常状態ファイルと同一とする

## [振り返り](../../../../agent-toolkit/skills/review-session/references/batch.md)・モード系

### 振り返りとモードの状態

- `AGENT_TOOLKIT_SESSION_REVIEW_LAUNCH`: hook→run-skill試行ID。`session-review/launches/`のID別JSON/logは起動元・プロセス同一性・診断・状態を持つ。`starting`=未受付、`accepted`=manifest移管、`settled`=正常終了/見送り、`notified`=次SessionStart通知済み。受付前終了を一度通知。実行中・未報告失敗以外は30日後sweepで回収
- `process_wi_skill_invoked`: process-wi起動フラグ。PostToolUse(Skill)・UserPromptSubmitが記録、`atk agents-exit-session`の機械可読応答で偽へ戻す。セッション終了まで保持する
- `autonomous_exit_invoked`: `_hooks/posttooluse.py`が`atk agents-exit-session`の実行・機械可読応答を記録。`_hooks/autonomous_exit.py`がprocess-loopのStop判定で参照し、未完了バックグラウンドタスクによる終了取消で偽、再実行で真とする。セッション中だけ保持し、スキル完了では再利用しない
- `stop_no_tool_turn_count`: `agent-toolkit/agent_toolkit/_hooks/busy_loop_guard.py`が、自セッションでツール呼び出しをしなかったターンの連続回数を記録し、常駐ループの停止判定に使用する。ツール呼び出しを観測したターン、委譲先やバックグラウンドタスクの完了を待つターン、停止工程の実行時は0に戻す。セッション終了まで保持する
- `stop_observed_entry_count`: 同フックが、Stop判定時点で観測済みだったセッション記録のエントリ数を記録し、次のターンで差分配信分のみを走査する起点として参照する。セッション終了まで保持する

## 進捗の点検系

- `progress_self_check`: `_hooks/progress_self_check.py`が、呼出主体（`agent_id`、無ければ`main`）ごとに次の3値をepoch秒で保持する。`started_at`は初回観測、`last_notified_at`は通知が最終出力へ採用された最後の時刻、`reserved_at`は期日到達で通知を生成した時刻である。
  PostToolUseとUserPromptSubmitが`update_state`で読み書きし、初回観測は記録だけにする。実発話・機械注入・PostToolUseを区別せず、`last_notified_at`から1800秒以上たった最初の観測で`source="progress-self-check"`の通知を生成し、`reserved_at`を記録する。
  生成しただけでは期日を消費しない。`notice.record_delivery`が最終出力へ採用した通知の生成時刻を`progress_self_check_delivered`へ記録し、次の観測がその最大値を`last_notified_at`へ畳み込む。
  `reserved_at`は60秒の間だけ同じ主体の並列な生成を抑止し、hook.pyがPostToolUseとUserPromptSubmitの処理の終了時に未採用の予約を解除する。Antigravityの保留通知は後の注入で配送記録を残すため、予約は失効時間に任せる。
  通知時刻はhookが通知を採用した証拠に限り、点検を実施した証拠には使わない。リセットは無く、生存期間はセッション状態ファイルと同一とする。

## 確認・操作スキル系

- `user_confirmation_skill_pending`: 各文脈における`agent-toolkit:user-confirmation-and-report`の起動案内が配送待ちであるかを示す真偽値
  `agent-toolkit/agent_toolkit/_hooks/rules_context.py`が、SessionStartの`source`が`startup`、`clear`、`compact`であり、かつ委譲先エージェントではない場合に真にする（`resume`や`fork`の場合は変更しない）。
  `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が人間の実際の発話を受けたときにこの値を参照し、真であれば同スキルの起動を促す注記を返す（機械的に注入されたターンでは参照も変更も行わない）。
  注記を返した時点で偽に戻す。また、PostToolUse(Skill)が同スキルの起動を観測した場合や、UserPromptSubmitがユーザーによる同スキルのスラッシュコマンド実行を観測した場合も偽に戻す。
  生存期間はセッション状態ファイルと同一とする。
- `operation_skill_delivered_agents`: 資料識別子から受信主体へのマッピング。`received-document:<JSON>`は論理資料・実体パス・必要範囲・受信版・範囲digestを保持する。分割受信は同じ主体・論理資料・版の行範囲を集約し、必要範囲が完成した時点で取得済みにする。部分記録は更新案内の根拠から除く。
  `document_reads.acquisition_state`が取得済み・取得後の更新・未取得を区別する。同じ必要本文を別実体から受信した場合も再利用し、他節の変更後も必要節の取得を維持する。
  旧形式の`read-trigger:<絶対パス>#sha256:<本文hash>`も、受信時の版のまま保持する。主体はpayloadの`agent_id`、無い場合は`main`とする。
  `read-update:<受信資料識別子>`は未受信の現在版の更新案内を実配送した主体を保持する。更新だけの場合は再書込を許可する。
  `read-trigger-skill:<絶対パス>#sha256:<本文hash>`はSkill起動の専用証拠とし、PostToolUseのSkill成功時に記録する。名指し参照のRead・Bashで得た本文取得と区別し、後続のスキル起動要求には専用証拠を求める。案内済みのキーも`read-guidance:read-trigger-skill:<絶対パス>#sha256:<本文hash>`で区別する。寿命・主体別の管理とclear・compactによるリセットは既存の取得状態と同じとする。
  同じ主体が取得済みの本文をEdit・MultiEdit・Writeで変更した成功では、編集応答の`originalFile`から変更前後の本文を確かめ、変更前に存在した種類の取得証拠だけを新hashへ引き継ぐ。参照取得で継承するのは本文取得の証拠に限る。`originalFile`が欠落・null、`staged`や`userModified`、編集失敗、別主体・外部変更・Bashの書換えは継承の対象外とする。ホスト応答の観測と再検証手段は`docs/development/audit-records.md`「.claude/skills/agent-toolkit-edit/references/session-state-and-flags.md：自己編集の取得継承：2026年11月2日」にある。
  `selected-read-target:<JSON>`は選択済みパス・見出しと選択元の`declarations`配列（`source`・`operations`・`kind`・`selector`）を保持し、持越し時に現行宣言へ接続する。対応の無い旧選択は保存したパス・見出しを解決し、失われた見出しを除く。
  `modified-agent-document:<絶対パス>`は変更文書、`review-reference:<絶対パス>`はprepareの参照資料、`read-trigger-role:<絶対パス>`は役割を保持する。選択情報は圧縮後も保持し、取得済み状態のみリセットする。
  `compact`では取得済みだった選択資料と必要範囲を候補にし、現在必要な範囲を予算内で一度案内する。
  `read-guidance-carried:<絶対パス>`は持越し候補を操作ごとの案内と遮断から外し、現在の操作が再選択したときに解除する。取得証拠は主体ごとにリセットする。
  `startup-input-target:<JSON>`は起動入力のパス・見出し・selectors・原資料のhashと配送本文の`body_sha256`を保持し、圧縮後の再配送に使う。`review-output:<絶対パス>`は未取得の保存JSON、`review-output-parsed:<資料識別子>`は解析済み版を示す。JSONから参照資料を登録した後は未取得の保存JSON登録を解除する。
  本文編集の検知対象は、編集ツール、Bashの静的なヒアドキュメント（heredoc）やヒアストリング（here-string）、インプレース置換、およびPythonスクリプトによる書き込みとし、生成出力の保存や単なるファイル複製は除外する。managed-temp配下の原稿に対してもこれと同じ基準を適用する。
  委譲先自身が使用する引き継ぎ記録ファイルについては、`AGENT_TOOLKIT_HANDOFF_PATH`が示す1件の絶対パスのみを、本文編集時のwriting-standards取得要求の対象から除外する。この除外後も取得状態は元の値を保つ。同一ディレクトリにある別ファイルや、環境変数が未設定の入力、引き継ぎ記録先以外を含む複数ファイルの編集については、通常の取得状態に基づいて判定する。
  `resolved-project-skill:<絶対パス>`は実際の取得入力から確定したproject skillの宣言元パスを保持する。起動時は正式入力の識別と実配送本文のdigestを確認し、受信した版を記録する。後日の更新は取得後の更新として区別し、取得済みの根拠は識別情報と本文の両方に求める。入力の識別情報と宣言元は会話圧縮時にも保持される。
  `startup-context-observed`は、正式な起動入力の取得先と役割を登録した主体を保持し、圧縮後に過去の履歴本文が二重計上されるのを防ぐ。`startup-context-reset`は、最初の操作より前に発生したclearやcompactについても記録する。これにより、同じ記録で本文取得の前提が誤って復元されるのを防ぎ、実体指定の再取得へつなげる。
  `read-guidance:<取得識別子>#heading:<必要節>`は、初回の未取得案内を実配送した主体を保持する。
  予約は最終出力へ採用した場合だけ確定する。未取得が続いても同じ案内を反復せず、再書込は許可する。取得証拠は同じ主体が実際に受信した本文だけから記録する。
  純粋Read・静的Bash読取・Skill起動では依存案内と未確定条件の候補通知を保留し、次の関連する非取得操作で必要範囲を再評価する。
  候補は役割・選択済み資料・同じ主体が取得した宣言元へ限定し、同じ条件と必要範囲を統合する。受信判定は`document_reads.acquisition_state`を再利用する。
  Claudeは実tool_response、Codexは後続PreToolUse時点の外側配送本文を観測する。取得の記録は、応答に実際に含まれた必要範囲に限る。
  ClaudeメインのSkill起動要求、名指し参照の内容取得、WebFetchのURL別出典確認は分離する。
  正式なstartup-documentは識別と本文を意味上で確認して記録する。
  選択は`ReadTrigger.condition`の自然文と役割を保持する。他役割の行を除き、判断契機では`read:`・`section:`の一致だけで自然文条件を確定しない。`confirmation`に一致する確認ツール呼出しと本文で識別したWI保存種別は、その操作で確かめられる条件だけを確定する。`AskUserQuestion`・`request_user_input`・`request_user_input_async`の呼出しは確認本文・選択肢の起草契機を観測したものとして扱う。他の未確定な自然文条件は確認候補とする。同一パスの節を集約し、宣言修復後の選択は現行宣言へ接続する。圧縮前の候補は現在の必読案内と遮断の根拠から除外する。
- `document_read_candidates`: PreToolUseで準備した、主体ごとの既知資料・必要範囲・受信前の版とdigest。Readの明示対象も含め、選択前の取得と観測前の外部更新に対応する。保存はdigestなどのメタデータに限定する。compact時はメインの候補を除く。
- `document_read_pending`: Codexの要求位置・生産出力上の位置・版・行digest・必要範囲の主体別候補。同じ外側配送の全ブロックで受信行と回数を確かめ、対応する要求位置だけを取得記録へ反映する。別位置の同文候補を区別できなければ取得済みにせず、対応しない候補を次の読取へ流用しない。本文は保存せず、compact時はメインの候補を除く。
- `document_read_spools`: PostToolUseが保存先の版・元資料の要求位置とdigestを主体別に保持し、保存先の実読取で原本の位置へ対応付ける。退避だけでは取得にせず、別主体・保存本文の変更後には流用しない。本文は保存せず、compact時はメインの来歴を除く。
- `document_read_offsets`: 主体とrolloutの絶対パスから、観測済みのバイト位置への対応。Codexの後続PreToolUseが完成した外側出力行だけを差分走査して進める。compact時はメインの位置を末尾へ進め、以後に配送された本文を観測する。寿命はセッション状態と同じ。
- `result_read_pending`: `result_reads.py`が、Codexの内側で回収した結果の識別digestと判断資料を主体別に保持する。後続PreToolUseで同じ結果の外側配送を確認したときだけ消費し、選択資料へ接続する。結果本文を保存せず、compactでメインの未配送候補を除く。
- `result_read_files`: 結果回収の公開応答が示した本文ファイルと判断資料の主体別対応。保存先を提示しただけでは資料を選択せず、同じ主体の完全受領を確認したときに選択する。セッション状態と同じ寿命で保持する。

- `termination_evidence`: `agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が、終了工程に必要な証拠を作業単位で管理・保持する。保持する内容には、作業ごとの成果作業の種別、report-completionの起動証拠、ツールの呼び出し履歴、判断の記録、遅れて返ってくる応答を元の作業に対応付けるための未完了の呼び出し、ならびに判断の根拠となった人間の入力が含まれる。
  PreToolUse、PostToolUse、UserPromptSubmitがツール呼び出しと人間の入力を逐次更新し、Stopの`termination_order_advisor`が成果作業と起動証拠の対応関係を読み取る。報告本文、完了の自己申告、起動済みを示す独自フラグなどは保持しない。
  解決済みの呼び出しや入力は更新のたびに除外し、バージョンや形式が合致しない値は破棄してStop判定ログに診断情報を残す。
  生存期間と継承の扱いはセッション状態ファイルと同一とし、別の会話から継承した値は`session_id`の一致を確認してから使用する。
  - 成果作業と起動証拠: 成功した対象スキルの起動、リポジトリに対する自身の編集作業、計画ファイルの保存、WIの保存、commitやpushなどを`deliverables`へ記録する。成功したreport-completionスキルの起動（Claude Code）や、外側で必要本文の配送を確認した`SKILL.md`の読み取り（Codex）は、`completion_report`へ記録する。Codexの成功した読取は`pending_reads`に所有plugin・実体・版・元の呼出しを保留し、共通の取得記録で本文受信を確認して開始証拠へ変える。compactで保留を除く。起動証拠を取得済みの作業に対して、新しい人間の入力があった後にさらに成果作業が発生した場合は、別の新しい作業として管理を開始する
  - 待機の許可: 成果作業が待機している非同期処理の実行中に、Stop hookが待機を許可する条件は`agent-toolkit:report-completion/references/claude-hooks-stop.md`「終了工程の証拠」に定める。本キーは作業の開始位置（UserPromptSubmitが記録したtranscriptのサイズ）を保持し、Stopがその判定時に参照する
  - 成果作業の対象: 観測されたリポジトリのルートパスを`repositories`に保持する。直接実行されたGit操作については`-C`などの引数を含めて対象リポジトリを取得し、シェルのディレクトリ移動や環境変数の前置代入によって対象を取得できない操作については`null`を保持する。対象が単一のリポジトリであると確定できる作業に限り、プロジェクト規範に基づく終了工程の省略指定を受理する
  - 委譲先に対するwait判定: `async_targets`とその所有者を確認し、観測義務とは独立して有効なCLI待機所有権や対象登録の有無を共通の読み取り処理で確認する。`pending_observation`が偽であっても待機が有効であれば状態を保持し、両方の処理が完了した段階で証拠不足の判定へ戻す
  - 自身の編集作業: 成功したEdit、Write、`apply_patch`、および既存の静的解析で確定したBashによる書き込み先を、作業ごとの`edits`にリポジトリおよび相対パスとして記録する。Claude Codeの協調モードにおけるメインエージェントでは、Stop hookがこの編集集合と終了時のGit状態を比較し、公開範囲が確定していない編集が含まれている場合は遮断通知に添えて提示し、他主体による差分を誤って取り込まないようにする
  - 公開範囲: メインエージェントが元の指示入力または適用済みの認可に基づいて判断した公開範囲を`publish_scope`に記録する。記録手順は`agent-toolkit:user-confirmation-and-report`の`references/publish-scope.md`に従う
  - プロジェクト規範による省略: メインエージェントが`omit-completion`で指定したエージェント向け文書の絶対パス、記録処理がそのパスから読み取った全文、対象リポジトリ、および人間の入力識別子を、作業ごとの`omit_completion`に保持する。`input_id`が省略されている場合は、現在の会話における直近の人間の入力に対応付ける。記録時には、観測された成果作業が単一のリポジトリに対するものであること、規範ファイルの所在、現在のメインエージェント・会話・入力の対応関係を確認し、明示された引用文（`quote`）が規範の全文と一致しているかも確認する。Stop hookにおいても規範ファイルの実体と入力の対応を確認する。有効な省略情報を持つ作業に限り、起動不足の判定を免除する。この免除は次回の判断時に解除され、新しい人間の入力後に発生した成果作業は新規作業として扱われるため、省略設定は引き継がれない。エージェント向け文書の変更や消失が確認された場合は、免除を無効化する。具体的な手順は`agent-toolkit:report-completion`の`references/termination-evidence.md`に従う
  - 元の入力と再突入: `last_human_input`には直近の人間の入力を保持し、自動通知による再開後であっても元の依頼への帰属関係を維持する。`--context`は、人間の入力の有無にかかわらず現在の会話の`session_id`と作業の`work_id`を返し、その入力を観測できた場合のみ`input_id`と`quote`を添え、観測できていなければ両者を`null`とする。証拠が存在しない場合、別会話や旧バージョンの値である場合、現在のメインエージェントとして特定できない場合、あるいは作業も人間の入力も存在しない場合は取得を拒否する。取得しても判断に必要な証拠条件は緩まず、人間の元の入力、実際の失敗、所有する委譲先の待機状態の検証は、各actionごとの記録処理において厳格に行われる。生存期間と継承の扱いは本キー全体と同一であり、公開範囲、起動証拠、および現在のGit状態から不要な判定を除外する

## hookの診断計測

- `command_timing`: `_hooks/command_timing.py`がシェルコマンドのPreToolUse時刻をsession・呼出主体・tool_use_idで対応付ける。PostToolUse/FailureとClaude背景完了のUserPromptSubmitが、全体の実行時間または観測経過時間を一度だけ通知する。保持対象は、実行中の開始時刻と背景task-id、完了後の重複判定用tool_use_idとdoneに限定する。見直し用履歴は主体ごとの同じ計測種別の直近3件だけとし、60秒未満・未計測・種別変更でリセットする。識別子はsession状態と同じ寿命で保持し、再開・継承で重複を防ぎ、clear・期限切れ回収で除く。hook自身の診断計測とは独立する。

hookの診断計測はセッションのmanaged-tempにある`hook-timings.jsonl`へ保存する。
`timing.py`がイベント・処理・区間・単調時計の所要・成否だけを記録し、同領域の`hook-timings.lock`で並行更新を直列化する。
上限64KiBに達すると直近の完全な行だけを残す。保存対象は処理の識別情報と測定値に限る。
セッションmanaged-tempと同じ7日保持で回収する。計測の用途は性能診断に限る。

## 通知反復系

Codex PreToolUseの長い追加通知は、既存のsession managed-tempへ全文を保存し、短い保存先参照を提示する。参照の提示は既存の通知反復抑止と取得案内の提示へ記録する。資料本文の取得証拠は、当該資料の外側配送を観測した既存の記録から確認する。

- `persistent_warning_fingerprints`: `_atk/outcome.py`が、共通起動の持続状態ならびにhookの引数不整合に関する警告本文のフィンガープリント（指紋情報）を記録する。同じ会話で初めて報告する内容だけを出力する。CLIの識別子はowner、Claude Code、Codexの順序で決定し、hookは入力の`session_id`を使用する。委譲先のスイープに関する警告は記録と出力を省略する。生存期間はセッション状態ファイルと同一とする
- `notice_fingerprints`: `notice.py`が、hook、呼出主体、操作範囲、発生原因ごとに通知本文と次操作のフィンガープリントを保持する。整形時は候補を予約し、最終出力に採用した非空本文だけを`emit_response`・`record_delivery`で記録する。破棄・ホスト変換による除去・AGYでの保留では未配送状態を維持する。同一条件の通知は初回のみ配信し、対象・原因・対処の変化を新しい通知として扱う。`notice_cycle`は未判定条件を保持し、解消を確認した原因だけを`resolve_notice`で解除して再成立時に通知する。遮断は実際の操作条件で決め、本キーや旧`warn_notice_counts`の回数だけで昇格させない。生存期間はセッション状態ファイルと同一とする
- `response_language_retry_by_actor`: `pretooluse/dispatch.py`が呼出主体から直前のツール名・入力全体のSHA256へのマッピングを保持する。引用・素材を除く説明が日本語に適合する入力を記録し、同じ主体・同じツール・同一入力の直後の再発行で回復に使う。対象外の操作や説明の不適合でその主体の値を消し、別の対象入力では直前値を置き換える。別主体の値は回復に使わない。英語の説明は同一再発行でも遮断する。生存期間の上限はセッション状態ファイルと同一とする

## agents_server連携系

- `agents_server_cwd_by_session`: `session_id`ごとの空でない作業ディレクトリの絶対パス（cwd）。`send_message`と`kill`の事前チェックおよびPostToolUse更新に使う。欠落時は同じ識別子への成功した`show`応答で、対象自身と`live_child_sessions`のcwdを回復する。永続session登録簿の`launcher_session_id`と呼出会話、対象の`launcher_agent_id`と呼出主体を照合する。`root_session_id`や変更可能な`owner_agent_id`だけでは所有の根拠にしない。所有不明・他者・応答ID不一致・空や相対cwdでは回復せず、遮断を保つ。案内はshow照会、所有確認後の元操作再発行へ接続する
- `agents_server_sessions`: `session_id`ごとの公開状態および内部状態を保持する。生存期間はセッション状態ファイルと同一とする
  - 記録する値: 公開状態として`session_id`、`status`、`kill_requested`、`pending_observation`、`owner_agent_id`、`model_type`、`error`、`agent_message`を保持する。内部状態は`turn_id`と`launcher_agent_id`である。`pending_observation`は未観測タスクの有無、`owner_agent_id`はそのタスクを発生させた主体、`error`と`agent_message`は終了時のエラーと返却本文を表す。`launcher_agent_id`は成功したstartの呼出主体を保存し、send_messageでは既存値を保つ。旧レコードで欠けていれば所有不明としてcwd回復に使わない
  - `model_type`の決定ロジック: 起動入力で指定された`model_type`を優先し、指定が省略された場合は、`start`の`task`では共通パーサーが読み取る役割文書のfrontmatterから取得し、その他の`mode`では各modeごとのデフォルト種別を適用する。ファイル名対応表は過去の旧文書を読み取る互換性維持のためだけに使用する。統合前の古い名称で記録されている開始履歴は、対応する`mode`として扱う
  - レコードの生成と更新: `start`の成功レスポンスを受信した時点でレコードを生成し、`send_message`、`kill`、`wait`の各レスポンス受信時、ならびにBash経由で`atk agents wait`が実行された際に更新する
  - 失敗の除外: `observe_tool`の共通境界で、全観測操作の`PostToolUseFailure`、MCPの`isError`・`is_error`、JSON包装内の失敗、`ToolError:`本文を除外する。委譲先状態、cwd、root alias、待機登録、終了ログは更新しない。正常なshowやkillが返す終了session自身の`error`はツール実行失敗と区別する
  - `pending_observation`を真にする契機: 各開始操作の成功レスポンスを受信した時点、および`delivery`が`reply_started`または`reply_ambiguous`である`send_message`のレスポンスを受信した時点とする。なお、`steered`は実行中のターンへ追加指示を届けるだけで`turn_seq`を変えないため、対象外とする。呼び出し主体はhookペイロードの`agent_id`（これを持たないメイン会話では`main`）から`owner_agent_id`へ記録する。呼び出し主体の判別に`transcript_path`を用いない（`agent-basics:writing-standards`の`references/claude-hooks.md`「hookスクリプトの基本プロトコル」を参照）
  - `pending_observation`を偽にする契機: `kill`の成功レスポンス（バックグラウンドタスクへの移行通知を含む）を受信した時点と、Bash経由の`atk agents wait`が完了した時点とする。`待機中:`表明と`send_message`の受理は観測ではないため、真のまま保持する。CLIの`atk agents wait`は引数として特定のセッションを受け取らず、呼び出し主体が所有している既存記録の全セッションを一括して観測済みに更新する。偽という値は「現時点で未観測のタスクが存在しない」ことを意味するため、新しいタスクを配送した場合は再び真に戻る。`owner_agent_id`は次のタスクが発生するまで保持される
  - 利用用途: Stop判定において、`pending_observation`が真であり、かつ`owner_agent_id`がStopを呼び出した主体と一致している記録を未観測警告の対象とする。観測を試みた後の継続は後掲「待機の継続」で別に判定する。CLI待機中については、ルートセッションが所有する待機権限が有効であることをStop hookが確認し、警告から除外する。現在のセッションがagents_serverのClaude・Codex委譲先である場合に限り、直前の応答の`待機中:`表明が指すセッションもそのStopの判定だけで除外し、保存した観測履歴は変更しない。終了工程における委譲先へのwait判定でもこれと同じ読み取り処理を使用し、観測義務と有効な待機状態を区別する。なお、`status`が`running`であるレコードの件数自体を、待機をブロックするための判定入力としない
  - レコードの削除: `stop`ツールの成功レスポンスを受信した時点で、その`session_id`のエントリを削除する。`stop`は実行中のターンを持つセッションや未終了のセッションにはエラーを返すため、未完了のセッションの記録は失われない
  - 待機の継続: `pending_observation`が偽でも、同じ呼出主体が所有する待機対象登録に残り、既存のsession登録簿が実行中と解決する対象は、現在の待機・自動再開を別に確認する。`agents_server_observations.retained_running_session_ids`が同じwait identity・登録簿を使い、advisorはCLI待機ロックも自動再開もない場合だけ非遮断で案内する。判定には既存の状態と登録を再利用する
  - 状態ファイルに保持しない情報: 結果を回収済みであることを示す専用フラグや、thread IDをハッシュ化した状態ファイルなどは保持しない

## バックグラウンドタスク系

- `background_task_ids`: PostToolUseが、自セッション内のツール呼び出しの応答から取得したタスクIDを重複なく記録する。この記録を「バックグラウンドタスクの所有記録」と呼ぶ。ここでいうバックグラウンドタスクとは、BashやAgentを背景で実行した非同期処理を指す。記録の契機は以下の4つであり、自身の呼び出しに対して識別子が返されたことが所有の根拠となる
  1. Bashで`run_in_background`を指定した際のレスポンス（成功・失敗を問わず）
  2. 実行時間の上限到達やユーザーの操作によってバックグラウンドへ移行したBashのレスポンスに含まれる構造化データ`backgroundTaskId`
  3. MCPツールの呼び出しがバックグラウンドへ移行した際にホストから送られる移行通知。行頭から始まる`MCP tool "<ツール名>" is still running after <秒数>s. It was moved to the background as task <ID>`という形式の通知のみを記録対象とし、フォアグラウンドの出力本文の途中に現れる同一の文言は除外する
  4. `Agent`や`Task`のバックグラウンド起動のレスポンス（`status`が`async_launched`）から返された`agentId`

  PreToolUse(TaskStop)は、停止対象のタスクが自セッションのバックグラウンドタスクであるかどうかの判定にこの記録を参照する。また、PreToolUse(Bash)も未完了タスクの出力ファイルを読み取ろうとした際の警告判定の前にこの記録を参照し、タスクが1件以上記録されている場合に限りtranscriptを読み取る。なお、出力先ファイルのパスは状態ファイルには記録せず、`background_tasks.py`と同様に起動集合と完了集合から特定した未完了のバックグラウンドBashについて、起動時の`tool_result`本文から取得する。この記録はセッション終了まで保持する。
- `queued_notification_notified_ids`: Stopの`queued_notification_advisor.py`が、最上位transcriptのキューに残っている未配信の完了通知に関して、既に案内した通知の識別子を重複なく記録する。
  識別子には`<task-id>`を用い、存在しない場合は`<tool-use-id>`、どちらも存在しない場合は通知本文そのものを使用する。AgentやTaskの起動記録に対応する通知については返却メッセージの確認を案内し、それ以外は出力ファイルの読み取りを案内する。他のStop判定によるブロックが存在する場合に限り、同一通知に対する警告を1回のみに制限するための判定にこのキーを参照する（繰り返し警告すると、ツールを呼び出さないままターンが継続してしまうループに陥るため）。他のブロックが存在しない場合は、案内済みであるかどうかにかかわらず現在未配信の通知を採用し、ホストによる配送を待って次のターンへ進む。警告回数と本キーは実際に警告する場合だけ更新する。セッション終了まで保持し、リセット処理は設けない
- `task_stop_blocked_at`: PreToolUse(TaskStop)が停止操作を遮断した時刻（POSIX秒）。同フックが再実行許可ウィンドウを判定する際に参照する。セッション終了まで保持し、リセットしない
- `stall_detection_completed_at_by_task`: `record_stall_detection.py`がプロセスの停滞検知を完了したタスクについて、完全なタスクIDをキー、検知完了時刻（POSIX秒）を値として記録する。PreToolUse(TaskStop)は、完了時刻から5分以内の対象タスクに限り停止可能と判定し、期限切れとなったエントリは除外する。停止処理が成功したPostToolUse(TaskStop)が該当エントリを消費し、停止に失敗した場合は同じ5分間の枠内で再試行できるようにエントリを保持する

## UWI系

- `uwi_answered_by_repo`: PostToolUseが記録する回答済みUWIファイル名の一覧。データ構造は`{エージェント識別子: {対象リポジトリID: ファイル名一覧}}`という2段階の辞書形式とし、`agent_id`を持たないメインエージェントの会話では`main`をキーとして使用する。メインとサブエージェントのフック呼び出しは同じ`session_id`で届くため、エージェント識別子ごとに分けないと、一方が回答差分を消費して他方へ通知が届かなくなる。
  cwdが空、保存先が未解決、後述のフィンガープリントが前回と同一、リポジトリIDが未解決、走査が不完全な場合のいずれかに該当するときは更新しない。前回の一覧に含まれていなかった新規の回答済みファイル名を通知するための判定に使用し、セッション終了まで保持する。リセットは行わず、一覧の更新によってのみ状態を進める。
- `uwi_fingerprint_by_repo`: PostToolUseが記録するアクティブな状態ディレクトリ（`inbox`、`processing`）の内容変更フィンガープリント。データ構造は`{エージェント識別子: {作業ディレクトリ: 指紋文字列}}`という2段階の辞書形式とし、前回と同一である場合はディレクトリの走査および回答差分の検出処理をスキップする。回答済みファイル名一覧の更新と同時に記録され、セッション終了まで保持する。

上記の2つのキーについては、現行キーが存在しない場合に限り、改名前の古いキー名である`tbd_answered_by_repo`および`tbd_fingerprint_by_repo`を読み取って状態を引き継ぐ。書き込みは常に現行のキー名へ行う（パッケージ更新をまたいで稼働中のセッションが、改名前のキーで基準値を持つことがあるため）。

## Antigravityのイベント間状態

Antigravity向けの6つのイベントアダプターは、共通のセッション状態ファイルへ以下の値を保存する。
値の更新および一括消費は`update_state`の排他ロック区間内で実行し、不要になったデータは既存のTTLスイープ処理によって回収される。

| キー | 型 | 書き込みと消費の契機 |
| --- | --- | --- |
| `antigravity_pending_contexts` | `list[str \| {text, receipts}]` | `SessionStart`、`PreToolUse`、`PostToolUse`が本文と配送予約の識別情報を蓄積する。旧文字列も読める。`PostInvocation`と初回の`PreInvocation`で一括消費し、最終応答の`injectSteps`へ実際に採用した時点で配送を記録する。保留時と内部handler出力時は記録しない |
| `antigravity_last_user_step` | `int` | `PreInvocation`が処理済みとなった`USER_INPUT`の`step_index`を記録し、同一発話に対する二重処理を防止する |
| `antigravity_stop_continuing` | `bool` | 直前の`Stop`が`continue`を要求したという事実を保存し、共通入力における`stop_hook_active`の値へ正規化する |
| `agy_cwd` | `str` | `SessionStart`が正規化されたワークスペースのcwdを保存し、`session_records`が記録ファイルのパス解決に使用する |
| `agy_transcript_path` | `str` | `SessionStart`が`transcriptPath`を保存し、`session_records`が記録の読み取りに使用する |
| `antigravity_subagent_ids` | `list[str]` | 親エージェントの`PostToolUse`が、`invoke_subagent`の返却値から作成済みの`conversationId`を登録する。件数が一致した`manage_subagents`のactive一覧、または`Stop`における`fullyIdle=true`によって終了を観測する |

自身が所有するsubagentの起動については、共通の`background_task_ids`にも登録する。
`background_task_ids`は起動したタスクの所有履歴としてTTL満了まで保持し、終了の観測時にも削除しない。
active一覧の照会結果や`fullyIdle`による状態の更新は、`antigravity_subagent_ids`へ反映する。
なお、Antigravityには存在しないイベントである`SubagentStart`や`SubagentStop`の直接購読は行わない。

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/SKILL.md` |
