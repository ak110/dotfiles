# セッション状態ファイルとフラグ

本書は、配布物のhook間で共有する状態ファイルの設計方針、生存期間、排他制御と、各フラグの記録元・参照先を定める。
状態ファイルのパスは`{state_dir}/hook-sessions/claude-agent-toolkit-{session_id}.json`とする。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選定した資料の指定範囲を取得してから、その基準に基づく具体的な判断へ進む。起動時の行に示された資料は、起動本文とともに配送される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-stop.md#「終了工程の証拠」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks-stop.md#「終了工程の証拠」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/publish-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/publish-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/termination-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks.md#「hookスクリプトの基本プロトコル」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/claude-hooks.md#「hookスクリプトの基本プロトコル」` |

## 状態ファイルの設計

hookは呼び出しごとに別プロセスで起動されるため、Claude CodeやCodexのhook間の情報共有にはセッション単位の状態ファイルを使う。

- パス規則: 本書の冒頭に示したパスを、`_common/state_paths.py`の`state_dir()`と`payload["session_id"]`から組み立てる
- 置き場所の選定: 必要な生存期間と共有範囲から選ぶ。セッション再開時やホスト再起動後の判定で読み取る記録、およびその排他制御に用いるロックファイルは、同一の状態ディレクトリに配置する。一時ディレクトリに配置してよいのは、消失しても判定に必要な情報が失われない診断ログや、再生成可能なキャッシュなどに限る
- 旧位置からの移行: 新しい配置場所に状態ファイルが存在しない場合に限り、排他ロック下で、同じ`session_id`を持つ旧配置場所（`tempfile.gettempdir()`配下）のJSONファイルを新位置へ移行する。通常の読み取り、更新、期限切れ回収、および前身状態の継承は新位置のファイルを使う
- データ形式: 単一のJSONオブジェクトとする。新規フラグのキー名はsnake_caseとし、既存のフラグ名は読み取り側との契約としてそのまま維持する
- 読み書きの契機: 書き込みはPostToolUseで観測したイベント（スキル呼び出しやバックグラウンドタスクの起動など）で行い、読み取りはPreToolUse、UserPromptSubmit、Stopにおける判定材料として利用する
- 破損・不在時の扱い: 破損・不在のファイルは空の辞書として扱い、安全側の判定へフォールバックする
- `session_id`の切り替え: 現行の状態ファイルが存在しない場合に限り、transcriptを先頭から走査し、状態ファイルを持つ別の`session_id`が一意に特定できるなら、その全キーを継承して継承元を記録する。現行の状態ファイルがある通常時は、存在確認だけで終える
- フックイベント間の同期: コマンド文字列の完全一致とハッシュ値比較の組み合わせなど、複雑な多段同期を状態ファイルに持ち込まず、対象ファイルの実体を直接実行・直接読み取りして状態を確かめる。構造化した記録を保存するのは、記録元と利用先が同一のフック内で完結する場合に限る
- フラグ仕様の文書化: 各フラグの用途、書き込み元、読み取り元、寿命、リセット処理を後掲の各節に記載し、新規フラグを追加する際も同様に併記する（努力目標）。同一の状態ファイルを共有する個人フックのフラグについても同様に扱う
- 状態JSONの削除契機: 状態ファイルを削除するのは、期限切れの定期回収と、終了理由が`clear`のSessionEndだけとする。SessionEndは`--continue`、`--resume`、`/resume`で同一の`session_id`に復帰する場合にも発火するため、他の理由で削除すると再開後の記録が失われる。期限切れ回収の対象は最終更新から一定期間が経過した通常の状態ファイルとし、`clear`時は排他ロック下で、対象セッションの新旧両方の配置場所にある状態ファイルを削除する
- サブエージェント起動の判定: サブエージェントが起動されたかどうかの判定は、`tool_name in ("Agent", "Task")`を唯一の信頼できる情報源（SSOT）とする。サブエージェント側で更新された状態は委譲元へ自動伝播しないため、親エージェント側で必要な値は完了報告の構造化フィールドから厳密に解析して取得する

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
- WI投入作業の属性情報は、`session-activity/submissions/{session_id}.json`へ会話ごとに保存する。保存項目は、バージョン番号、会話ID、作業種別（`add-awi-by-user`）、正規化された投入先URLの一覧、更新時刻である。このファイルは`atk wi submission begin`が作成・更新し、`atk wi submission end`が削除する。hookのSessionEndでは削除せず、セッション再開時にその属性を再び有効化できるようにする
- 開始前判定では、投入先がprocess-loopの対象リポジトリ群と重複している属性を持つ会話、およびその子孫の活動のみを待機理由とし、属性を読み取れない会話や属性を持たない会話は除外する
- 対象外とする会話とその子孫の判定には`_common/session_parents.py`による親子関係の解決結果を用い、process-loopの共通環境変数などの識別印だけで別ループの会話まで誤って除外しないようにする

## managed-tempの状態

- 各セッションのmanaged-tempディレクトリ名は、一時ルート直下の`session-<sha256(session_id)の16進表記の先頭16桁>`とし、すべての導出で共通の関数を使って決める。新しい領域の状態は、一時ルート直下に実体として存在するディレクトリのみで管理する
- 旧バージョンが作成した状態ルート（`<state_dir>/managed-temp`）の`.sessions/<sha256(session_id)>`が一時ルート直下の実在ディレクトリ（非シンボリックリンク）を指す場合は、旧パスを同一会話の領域として再利用し、参照処理は読み取りのみに限定する。旧マーカー（`.agent-toolkit-managed-temp.json`）、旧登録簿、`.sessions`参照、cleanupロックの名前変更や一括削除は行わない。定期スイープが回収条件を確かめ、対象ごとのロックを非ブロッキングで取得できたものだけを個別に回収する
- 7日周期のスイープ処理における次回の確認時刻は、状態ルートの`.sweep/schedule`内に、一時ルート直下のディレクトリ名をキーとする単一のテーブル形式で記録する

## 完了報告からの状態読み取り

完了報告の本文から機械的に状態を読み取る場合、検出対象は報告末尾にある連続した記録行ブロック、または最終行のみに限定する（本文中の引用で状態フラグが誤って真になるのを防ぐため）。
記録行かどうかの判定には、既知のキー（例: `invoked_subagents`、`codex_unavailable`）を定義した許可リストを使用する。英字キー全般に一致する汎用の正規表現は使わない（末尾付近の「`キー:`」形式の散文を記録行と誤認しないため）。

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
- 消費元: `hooks/usage_limit_resume.ts`の`turn.complete`が、`requested`を`consumed`に変えて既存の再開予約処理へ接続する
- 初期化と生存期間: 初期状態ではファイルを作成せず、`requested`の記録だけを予約実行の根拠とする。通常のSessionEndではファイルを保持し、`clear`や期限切れ回収時の扱いは前述のFunction hooks用ファイルと同様とする

## 状態の継承

- `inherited_from_session_id`: 現行の`session_id`に対応する状態が存在しない場合に、一意に特定された前身の`session_id`を記録する。共通hookの起動処理が、前身の全状態キーの引き継ぎと同時に1回だけ書き込みを行う

## plan系

- `plan_mode_skill_invoked`: plan-modeスキルの起動を記録し、計画ファイルの構造に対する自動チェックを適用するかどうかの判定に使用する
- `current_plan_file_path`: PostToolUseが、計画ファイルの編集時や公開された計画作成コマンドの出力からファイルパスを記録する。PreToolUse(Skill)がplan-mode起動時にこの値を消去する。UserPromptSubmitのコマンドhookがセッション名を選択する際にこのパスを参照する
- `last_hook_session_title`: UserPromptSubmitのコマンドhookが設定したセッション名。現在のセッション名が空であるか、この値と一致している場合に限り、新しい種別や計画名への更新を許可する。同hookが通常状態ファイルへ排他ロック下で保存する。通常状態ファイルと同様の保持期限および`clear`のタイミングで消去される
- `session_title_kind`: PostToolUse(Skill)、およびスキルの手動起動を受け取ったUserPromptSubmitが記録する命名用の種別（`process-wi`、`single-lane`、`add-awi`）。UserPromptSubmitのコマンドhookがセッション名を選択する際に参照する。process-loopの環境識別印と会話IDが一致している場合は`process-wi`を優先する。生存期間は通常状態ファイルと同一とする

## 振り返り・モード系

- `process_wi_skill_invoked`: process-wiスキルの起動状態を示すフラグ。PostToolUse(Skill)およびUserPromptSubmitが記録し、`atk agents-exit-session`の機械可読な応答を受け取った時点で偽に戻す。セッション終了まで保持する
- `autonomous_exit_invoked`: `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`が、`atk agents-exit-session`の実行およびその機械可読な応答を記録する。
  `agent-toolkit/agent_toolkit/_hooks/autonomous_exit.py`がprocess-loopのStop判定時にこの値を参照する。
  同フックが、未完了のバックグラウンドタスクが残存していることを理由に終了要求を取り下げた時点で偽へ戻し、その後`atk agents-exit-session`が再実行された際に再び真となる。
  セッション状態の有効期間中だけ保持し、通常のスキル完了処理では再利用しない
- `stop_no_tool_turn_count`: `agent-toolkit/agent_toolkit/_hooks/busy_loop_guard.py`が、自セッションでツール呼び出しをしなかったターンの連続回数を記録し、常駐ループの停止判定に使用する。ツール呼び出しを観測したターン、委譲先やバックグラウンドタスクの完了を待つターン、停止工程の実行時は0に戻す。セッション終了まで保持する
- `stop_observed_entry_count`: 同フックが、Stop判定時点で観測済みだったセッション記録のエントリ数を記録し、次のターンで差分配信分のみを走査する起点として参照する。セッション終了まで保持する

## 確認・操作スキル系

- `user_confirmation_skill_pending`: 各文脈における`agent-toolkit:user-confirmation-and-report`の起動案内が配送待ちであるかを示す真偽値
  `agent-toolkit/agent_toolkit/_hooks/rules_context.py`が、SessionStartの`source`が`startup`、`clear`、`compact`であり、かつ委譲先エージェントではない場合に真にする（`resume`や`fork`の場合は変更しない）。
  `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が人間の実際の発話を受けたときにこの値を参照し、真であれば同スキルの起動を促す注記を返す（機械的に注入されたターンでは参照も変更も行わない）。
  注記を返した時点で偽に戻す。また、PostToolUse(Skill)が同スキルの起動を観測した場合や、UserPromptSubmitがユーザーによる同スキルのスラッシュコマンド実行を観測した場合も偽に戻す。
  生存期間はセッション状態ファイルと同一とする。
- `operation_skill_delivered_agents`: 資料識別子から、その資料を取得した主体（エージェント）一覧へのマッピング情報。既存のキー構成を継承し、宣言型配送における識別子は`read-trigger:<絶対パス>#sha256:<本文hash>`、節指定がある場合は`#heading:<節指定>`、分割読み取りの範囲は`#range:<開始>:<終了>`を付与する。本文およびハッシュ値は同一の読み取り時点のものを使用する。主体はhookペイロードに含まれる`agent_id`とし、これを持たない会話では`main`とする。`selected-read-target:<JSON>`は選択済みのパスと見出しを保持し、`modified-agent-document:<絶対パス>`は変更されたエージェント向け文書のパスを保持する。`review-reference:<絶対パス>`はprepareで生成された資料のパス、`read-trigger-role:<絶対パス>`は受領した役割を保持する。これらの選択情報は会話圧縮時も保持され、本文の取得済み状態のみがリセットされる。SessionStartの`compact`時は、本文取得状態をリセットする直前に、選択済みであり、かつ同一のパス・見出し・本文ハッシュ値で`main`が取得済みだった資料を、同一呼び出し内の一時候補として抽出する。出力予算に収まる候補のみを再配送し、予算を超過した資料については未取得として絶対パスと指定範囲を明示する。候補情報自体は状態ファイルへ永続化しないため、`clear`や次回の`compact`時は、その時点の取得済み状態のみから再計算する。`startup-input-target:<JSON>`は起動時に受け取った入力資料のパス、見出し、selectors（入力名の配列）、本文ハッシュ値を保持し、圧縮後の再配送に使用する。`review-output:<絶対パス>`はprepareの未取得な保存JSONパス、`review-output-parsed:<資料識別子>`は末尾まで取得して解析済みのバージョンを示す。JSONから実在する参照資料を登録した後は、未取得な保存JSONの登録を解除する。
  本文編集の検知対象は、編集ツール、Bashの静的なヒアドキュメント（heredoc）やヒアストリング（here-string）、インプレース置換、およびPythonスクリプトによる書き込みとし、生成出力の保存や単なるファイル複製は除外する。managed-temp配下の原稿に対してもこれと同じ基準を適用する。
  委譲先自身が使用する引き継ぎ記録ファイルについては、`AGENT_TOOLKIT_HANDOFF_PATH`が示す1件の絶対パスのみを、本文編集時のwriting-standards取得要求の対象から除外する。この除外で取得済み状態は付与されない。同一ディレクトリにある別ファイルや、環境変数が未設定の入力、引き継ぎ記録先以外を含む複数ファイルの編集については、通常の取得状態に基づいて判定する。
  `resolved-project-skill:<絶対パス>`は、実際の取得入力から確定したproject skillの宣言元パスを保持する。起動時の取得記録は、入力のメタデータと実際に配送された同一バージョンの本文が一致している場合に記録し、メタデータのみの抜粋は取得済みとしてはカウントしない。入力の識別情報と宣言元は会話圧縮時にも保持される。
  `startup-context-observed`は、正式な起動入力の取得先と役割を登録した主体を保持し、圧縮後に過去の履歴本文が二重計上されるのを防ぐ。`startup-context-reset`は、最初の操作より前に発生したclearやcompactについても記録する。これにより、同じ記録で本文取得の前提が誤って復元されるのを防ぎ、実体指定の再取得へつなげる。
- `termination_evidence`: `agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が、終了工程に必要な証拠を作業単位で管理・保持する。保持する内容には、作業ごとの成果作業の種別、completion-reportの起動証拠、ツールの呼び出し履歴、判断の記録、遅れて返ってくる応答を元の作業に対応付けるための未完了の呼び出し、ならびに判断の根拠となった人間の入力が含まれる。
  PreToolUse、PostToolUse、UserPromptSubmitがツール呼び出しと人間の入力を逐次更新し、Stopの`termination_order_advisor`が成果作業と起動証拠の対応関係を読み取る。報告本文、完了の自己申告、起動済みを示す独自フラグなどは保持しない。
  解決済みの呼び出しや入力は更新のたびに除外し、バージョンや形式が合致しない値は破棄してStop判定ログに診断情報を残す。
  生存期間と継承の扱いはセッション状態ファイルと同一とし、別の会話から継承した値は`session_id`の一致を確認してから使用する。
  - 成果作業と起動証拠: 成功した対象スキルの起動、リポジトリに対する自身の編集作業、計画ファイルの保存、WIの保存、commitやpushなどを`deliverables`へ記録する。成功したcompletion-reportスキルの起動（Claude Code）や、結果として本文が返された`SKILL.md`の読み取り（Codex）は、`completion_report`へ記録する。起動証拠を取得済みの作業に対して、新しい人間の入力があった後にさらに成果作業が発生した場合は、別の新しい作業として管理を開始する
  - 待機の許可: 成果作業が待機している非同期処理の実行中に、Stop hookが待機を許可する条件は`claude-hooks-stop.md`「終了工程の証拠」に定める。本キーは作業の開始位置（UserPromptSubmitが記録したtranscriptのサイズ）を保持し、Stopがその判定時に参照する
  - 成果作業の対象: 観測されたリポジトリのルートパスを`repositories`に保持する。直接実行されたGit操作については`-C`などの引数を含めて対象リポジトリを取得し、シェルのディレクトリ移動や環境変数の前置代入によって対象を取得できない操作については`null`を保持する。対象が単一のリポジトリであると確定できる作業に限り、プロジェクト規範に基づく終了工程の省略指定を受理する
  - 委譲先に対するwait判定: `async_targets`とその所有者を確認し、観測義務とは独立して有効なCLI待機所有権や対象登録の有無を共通の読み取り処理で確認する。`pending_observation`が偽であっても待機が有効であれば状態を保持し、両方の処理が完了した段階で証拠不足の判定へ戻す
  - 自身の編集作業: 成功したEdit、Write、`apply_patch`、および既存の静的解析で確定したBashによる書き込み先を、作業ごとの`edits`にリポジトリおよび相対パスとして記録する。Claude Codeの協調モードにおけるメインエージェントでは、Stop hookがこの編集集合と終了時のGit状態を比較し、公開範囲が確定していない編集が含まれている場合は遮断通知に添えて提示し、他主体による差分を誤って取り込まないようにする
  - 公開範囲: メインエージェントが元の指示入力または適用済みの認可に基づいて判断した公開範囲を`publish_scope`に記録する。記録手順は`agent-toolkit:user-confirmation-and-report`の`references/publish-scope.md`に従う
  - プロジェクト規範による省略: メインエージェントが`omit-completion`で指定したエージェント向け文書の絶対パス、記録処理がそのパスから読み取った全文、対象リポジトリ、および人間の入力識別子を、作業ごとの`omit_completion`に保持する。`input_id`が省略されている場合は、現在の会話における直近の人間の入力に対応付ける。記録時には、観測された成果作業が単一のリポジトリに対するものであること、規範ファイルの所在、現在のメインエージェント・会話・入力の対応関係を確認し、明示された引用文（`quote`）が規範の全文と一致しているかも確認する。Stop hookにおいても規範ファイルの実体と入力の対応を確認する。有効な省略情報を持つ作業に限り、起動不足の判定を免除する。この免除は次回の判断時に解除され、新しい人間の入力後に発生した成果作業は新規作業として扱われるため、省略設定は引き継がれない。エージェント向け文書の変更や消失が確認された場合は、免除を無効化する。具体的な手順は`agent-toolkit:completion-report`の`references/termination-evidence.md`に従う
  - 元の入力と再突入: `last_human_input`には直近の人間の入力を保持し、自動通知による再開後であっても元の依頼への帰属関係を維持する。`--context`は、人間の入力の有無にかかわらず現在の会話の`session_id`と作業の`work_id`を返し、その入力を観測できた場合のみ`input_id`と`quote`を添え、観測できていなければ両者を`null`とする。証拠が存在しない場合、別会話や旧バージョンの値である場合、現在のメインエージェントとして特定できない場合、あるいは作業も人間の入力も存在しない場合は取得を拒否する。取得しても判断に必要な証拠条件は緩まず、人間の元の入力、実際の失敗、所有する委譲先の待機状態の検証は、各actionごとの記録処理において厳格に行われる。生存期間と継承の扱いは本キー全体と同一であり、公開範囲、起動証拠、および現在のGit状態から不要な判定を除外する

## 通知反復系

- `persistent_warning_fingerprints`: `_atk/outcome.py`が、共通起動の持続状態ならびにhookの引数不整合に関する警告本文のフィンガープリント（指紋情報）を記録する。同じ会話で初めて報告する内容だけを出力する。CLIの識別子はowner、Claude Code、Codexの順序で決定し、hookは入力の`session_id`を使用する。委譲先のスイープに関する警告は記録と出力を省略する。生存期間はセッション状態ファイルと同一とする
- `notice_fingerprints`: 通知の整形処理において、hook、呼び出し主体、操作範囲、ならびに発生原因ごとに、通知本文と次回操作のフィンガープリントを保持する。同一条件の通知は初回のみ配信し、対象、原因、対処方法のいずれかに変化があった場合に新しい通知として扱う。同一の処理範囲が終了した時点で成立しなくなった原因を消去し、一度解消した問題が再発した際に再び通知できるようにする。処理を遮断すべきかどうかの判断は実際の操作条件に基づいて行い、本キーや旧`warn_notice_counts`の出現回数だけで遮断へ引き上げない。生存期間はセッション状態ファイルと同一とする

## agents_server連携系

- `agents_server_cwd_by_session`: `session_id`ごとの作業ディレクトリの絶対パス（cwd）。`send_message`および`kill`の事前チェック、ならびに各ツールのPostToolUseにおける状態更新に使用する。`show`の応答に含まれる稼働中の子セッションの識別子とcwdのペアも記録し、委譲元がその子セッションに対してメッセージの追送や終了要求を発行できるようにする
- `agents_server_sessions`: `session_id`ごとの公開状態および内部状態を保持する。生存期間はセッション状態ファイルと同一とする
  - 記録する値: 公開状態として`session_id`、`status`、`kill_requested`、`pending_observation`、`owner_agent_id`、`model_type`、`error`、`agent_message`を保持し、内部状態として`turn_id`を保持する。`pending_observation`はまだ観測を試みていないタスクの有無を示し、`owner_agent_id`はそのタスクを発生させた主体を、`error`および`agent_message`はセッション終了時のエラー情報と返却メッセージを表す
  - `model_type`の決定ロジック: 起動入力で指定された`model_type`を優先し、指定が省略された場合は、`start`の`task`では共通パーサーが読み取る役割文書のfrontmatterから取得し、その他の`mode`では各modeごとのデフォルト種別を適用する。ファイル名対応表は過去の旧文書を読み取る互換性維持のためだけに使用する。統合前の古い名称で記録されている開始履歴は、対応する`mode`として扱う
  - レコードの生成と更新: `start`の成功レスポンスを受信した時点でレコードを生成し、`send_message`、`kill`、`wait`の各レスポンス受信時、ならびにBash経由で`atk agents wait`が実行された際に更新する
  - `pending_observation`を真にする契機: 各開始操作の成功レスポンスを受信した時点、および`delivery`が`reply_started`または`reply_ambiguous`である`send_message`のレスポンスを受信した時点とする。なお、`steered`は実行中のターンへ追加指示を届けるだけで`turn_seq`を変えないため、対象外とする。呼び出し主体はhookペイロードの`agent_id`（これを持たないメイン会話では`main`）から`owner_agent_id`へ記録する。呼び出し主体の判別に`transcript_path`を用いない（`claude-hooks.md`「hookスクリプトの基本プロトコル」を参照）
  - `pending_observation`を偽にする契機: `kill`の成功レスポンスを受信した時点、Bash経由の`atk agents wait`が完了した時点、およびStop時に直前の応答に含まれる`待機中:`表明がそのセッションを指している場合とする。CLIの`atk agents wait`は引数として特定のセッションを受け取らず、呼び出し主体が所有している既存記録の全セッションを一括して観測済みに更新する。偽という値は「現時点で未観測のタスクが存在しない」ことを意味するため、新しいタスクを配送した場合は再び真に戻る。`owner_agent_id`は次のタスクが発生するまで保持される
  - 利用用途: Stop判定において、`pending_observation`が真であり、かつ`owner_agent_id`がStopを呼び出した主体と一致している記録のみを警告対象とする。CLI待機中については、ルートセッションが所有する待機権限が有効であることをStop hookが確認し、未観測警告の対象から除外する。終了工程における委譲先へのwait判定でもこれと同じ読み取り処理を使用し、観測義務と有効な待機状態を区別する。なお、`status`が`running`であるレコードの件数自体を、待機をブロックするための判定入力としない
  - レコードの削除: `stop`ツールの成功レスポンスを受信した時点で、その`session_id`のエントリを削除する。`stop`は実行中のターンを持つセッションや未終了のセッションにはエラーを返すため、未完了のセッションの記録は失われない
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
| `antigravity_pending_contexts` | `list[str]` | `SessionStart`、`PreToolUse`、`PostToolUse`が保留通知を蓄積し、`PostInvocation`でそれらを一括消費する。なお、初回の`PreInvocation`でも起動通知を消費する |
| `antigravity_last_user_step` | `int` | `PreInvocation`が処理済みとなった`USER_INPUT`の`step_index`を記録し、同一発話に対する二重処理を防止する |
| `antigravity_stop_continuing` | `bool` | 直前の`Stop`が`continue`を要求したという事実を保存し、共通入力における`stop_hook_active`の値へ正規化する |
| `agy_cwd` | `str` | `SessionStart`が正規化されたワークスペースのcwdを保存し、`session_records`が記録ファイルのパス解決に使用する |
| `agy_transcript_path` | `str` | `SessionStart`が`transcriptPath`を保存し、`session_records`が記録の読み取りに使用する |
| `antigravity_subagent_ids` | `list[str]` | 親エージェントの`PostToolUse`が、`invoke_subagent`の返却値から作成済みの`conversationId`を登録する。件数が一致した`manage_subagents`のactive一覧、または`Stop`における`fullyIdle=true`によって終端を観測する |

自身が所有するsubagentの起動については、共通の`background_task_ids`にも登録する。
`background_task_ids`は起動したタスクの所有履歴としてTTL満了まで保持し、終端の観測時にも削除しない。
active一覧の照会結果や`fullyIdle`による状態の更新は、`antigravity_subagent_ids`へ反映する。
なお、Antigravityには存在しないイベントである`SubagentStart`や`SubagentStop`の直接購読は行わない。
