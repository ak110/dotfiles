# API失敗と利用上限の待機の設計記録

[設計記録の索引](design.md)から参照する、API失敗と利用上限の待機の記録である。

Claude backendが受け取るAPI失敗の合成assistantメッセージと`SystemMessage(api_retry)`はモデルの活動を示さないため、`updated_at`を進めない。
失敗の種別、取得できたHTTPステータス、連続失敗の初回時刻と件数だけをsessionに保持し、状態ファイルへ射影する。
`show`と`list`は共通の射影から失敗の経過秒を算出し、通常のassistantメッセージで記録を削除する。
これにより、再試行の受信で活動経過が短く表示される状態を防ぐ。
親プロセスや外部ログから再試行を推定する案は、sessionの受信境界で既に失敗の種別を得られるため採用しない。
turn内の再試行通知は次の表で管理し、委譲先CLIの版を更新するときに、この表の通知と新しい版の通知を比べて種別・項目の変化を確かめる。

| 委譲先 | 情報源 | `api_error`への写像と解除 |
| --- | --- | --- |
| Claude | `error`付き合成assistantメッセージ | エラー種別と取得できたHTTP状態を記録し、正常なassistantメッセージで削除する |
| Claude | `SystemMessage`の`subtype: api_retry` | `error`の種別、`error_status`、受信時刻に`retry_delay_ms`を加えたUTCの`retry_at`を記録する。`count`は通知数でなく`attempt`とする |
| Claude | 最後の`RateLimitEvent`が待機対象の拒否である場合の429の`api_retry` | 種別を`usage_limit`とし、`limit_type`と`resets_at`を加える。解除時刻が報告に無い場合は`retry_at`を使う。429以外の再試行では通常のAPI再試行へ置き換える |
| Codex | App Serverの同じthread・turnの`error`通知で`willRetry: true` | `codexErrorInfo`の文字列またはvariant名を種別とし、variantが持つ`httpStatusCode`だけをHTTP状態へ写す。モデル活動とturn終了で削除する |
| Antigravity | 確認した公開stream-jsonの`init`・`step_update`・`result` | 1.2.9でturn内再試行の通知は確認できていない。版更新時は新しい通知と表の記録を比べて変化を確かめる |

Claude Code 2.1.306の`api_retry`は`attempt`・`max_retries`・`retry_delay_ms`・`error_status`・`error`を持つ。
`CLAUDE_CODE_RETRY_WATCHDOG`の下では5時間の利用上限でturnを終えず、解除予定時刻まで待つ。
process-loopのClaude起動はこの変数を設定し、agents_serverとその委譲先も継承する。
同じ試行の待機中も通知が反復されるため、通知数を試行数として数えない。
`retry_at`はMCPのlist・show、CLIのlist・waitへ同じ共通射影で返す。
通知の記録は活動時刻を進めない。
Codexの`willRetry: false`は既存のturn終了処理が扱い、通知から再開予定時刻を推定しない。

Claudeはturn途中の`api_retry`と`RateLimitEvent`の拒否を受信した時点で待機方針と代替接続を評価する。
再試行通知に利用枠が無い場合は、同じ会話の起動後に書かれた`system/api_error`から、試行番号・上限回数・待機時間・HTTP状態が通知と一致する利用枠情報を読む。
一般の429・529は通常のAPI再試行として記録し、正常なassistant応答で解除する。
利用上限の待機判定は`_atk/usage_limit_policy.py`の`should_wait`へ集約する。
`claude_usage_limit_wait`と`codex_usage_limit_wait`は`never`・`five-hour`・`always`を受理し、既定値は`five-hour`とする。
`five-hour`は`five_hour`だけを待ち、`always`は週次枠なども待つ。
Codexは通知の`window_minutes`から`five_hour`と`seven_day`を判別する。

429の再試行中は同じsessionの終了または正常なモデル出力を待つ。
`seconds_since_activity`はモデル活動がない間に増えるが、`api_error`が429を示す間はその値だけで委譲先を停止しない。
診断の可視化と委譲先の置換を別の判断に分けることで、利用枠の回復後に同じ実行を継続できる余地を残す。
Codexのturnが`start`の可用性確認を終えた後にサーバー側過負荷（`codexErrorInfo`の`serverOverloaded`）で終了する場合がある。
この場合は終了時の結果を既存の保留機構（`pending_result`と`awaiting_auto_resume`）へ保留し、待機の後に同じsessionへ継続の配送本文を新しいturnとして送る。
2026年10月3日に、約4.85時間続いたレーン担当のturnが過負荷で終了し、14秒後の再送も過負荷で終わった。
待機は15秒、30秒、60秒の最大3回とする（2026年10月4日、ユーザー指示）。
上限に達しても解けない場合は最後の失敗を現行どおり公開する。
このとき`_carry_over_unavailable_candidate`が候補を次回の起動の除外対象として記録し、委譲元の起動し直しで別のモデルへ移る。
待機中は`status`を`running`に保ち、`api_error`へ種別`serverOverloaded`とHTTP状態の不明（`null`）、連鎖の最初の時刻と回数を記録する。
この記録は`show`・`list`・状態ファイル・statuslineへ既存の射影で出力する。
対象を`serverOverloaded`へ限る。
利用枠超過と流量制限は解除までの時間が応答から定まらず短い待機で解ける根拠が無く、Claudeの529は観測していないためである。
可用性確認の間の過負荷は、会話の履歴を失わずに次の候補で起動し直せる既存の候補切替が扱う。
未観測の孫sessionが残るturnは孫の待機を優先する。
孫の終了後の再開本文が作業を再開させるためである。
待機中の`kill`は新しいturnを始めずに最後の失敗で終了し、委譲元の`send_message`は保留を確定して自動継続を取り消す。

Claude backendは`RateLimitEvent`から利用枠の状態・種類・解除予定時刻を保持し、初期化前の報告もsessionの作成時に引き継ぐ。
拒否された利用枠が待機対象なら結果を保留し、`api_error`へ`usage_limit`、HTTP状態、初回時刻、回数、`limit_type`と`resets_at`を記録する。
MCPの常駐監視は解除予定時刻に同じsessionへ継続を送り、再度の拒否では待機へ戻る。
解除予定時刻が不明か過ぎている場合は300秒ごとに確かめ直す。
待機には回数と総時間の上限を置かず、`auto_resume_deadline`と保持期限でも確定しない。
正常なモデル出力で利用枠の記録と`api_error`を削除する。
起動直後に待機へ入った場合も`start`は`running`を返す。
待機中の`kill`は保留した結果で終了し、`send_message`は保留を確定してreplyとして配送する。
解除待ちの拒否は除外記録へ残さず、旧版の理由`429`も除外の根拠にしない。
待機対象外の拒否は代替接続を評価し、候補の除外が必要な場合は`429:<種類>`を使う。
HTTP状態の429だけでは利用上限と流量制限を区別できないため、利用枠情報と待機方針で判定する。
過負荷の有限再試行と、利用上限の解除予定時刻までの待機は別々に管理する。
Claudeの接続候補は`_atk/claude_connection.py`へ集約し、認証の所在と役割別の使用順を分離する。
認証の所在は`claude_credits_env_file`・`claude_payg_env_file`、使用順は`claude_orchestrator_connections`・`claude_worker_connections`が持つ。
組み合わせで意味が決まっていた旧キーは、設定読込時に1回だけ新キーへ移して削除する（2026年10月29日、ユーザー回答）。
対象の旧キーは`claude_api_env_file`・`claude_orchestrator_api_only`・`claude_worker_api_first`・`claude_api_mode`である。
旧キーの環境変数と`get`・`set`は移行先を示して拒否する。
旧キーを互換入力として保持する案は、組み合わせの判定が設定と実装の両方を複雑にしていたため採らない。
4キーのどれも設定しない場合は`inherit`接続を選び、親環境とClaude Codeの通常の認証解決を維持する。
認証検出による起動拒否、認証変数の除去、apiKeyHelperの上書き、役割別割当と接続切替を行わない。
接続を管理しない環境で開始した会話と、接続情報もAPIパスも持たない旧形式の会話は、接続を管理しない間は通常の認証解決を使う。
接続を管理し始めた後は、`inherit`も使用順の種別ではないため次の継続で移す。
使用順は先頭から評価し、認証キーが未設定の種別は診断なしで除き、設定したのに空・不在・読取不能・形式不正、または認証・接続方式の変数を持たないenvファイルは修正方法を示して除く。
APIクレジットはClaude Codeを対象外とするため、`claude` CLIで起動するprocess-loopの統括担当は`credits`を除き、その旨を診断へ出す。
SDKで起動する委譲先とall-reposのリポジトリ担当は`credits`を使える。
通常のprocess-loopとall-reposのリポジトリ担当は同じ役割であり、起動手段がSDKでもオーケストレーターとして扱う。
可用性プローブ・本起動・再開を同じ接続で扱う。
いずれかのキーを設定して使える候補が1つも無い役割は、原因と設定方法を示して起動を止める。
直接起動のユーザー会話、Agentとfork、run-skill、commitはこの設定の対象外である。
オーケストレーターは起動時の接続を会話の中で変えず、通常の委譲先は同じsession ID・model・effortで使用順の次の接続へ移る。
待機対象の利用上限は解除を待ち、待機対象外で全候補が失敗した場合と認証なしを終了する。
新規sessionは毎回使用順の先頭候補から評価し、再開する会話は登録簿に保持した接続を使う。
保持した種別が現行の使用順に含まれない場合は、追送・自動再開・保存後再開で同じ会話IDのまま使用順の先頭の利用可能な候補へ移し、外した種別へは戻さない。
旧`api-only`（既存のsubscription会話も次の継続でAPIへ移し、上限解除後もサブスクリプションへ復帰しない）はこの規則で表す。
種別を区別する前に保存した`api`の会話は、保存済みのenvファイルが`claude_credits_env_file`と一致すれば`credits`、それ以外は`payg`として、保存済みのパスで再開する。
利用上限でサブスクリプションからAPIへ移した委譲先は、上限の解除予定時刻（不明なら既存の待機と同じ既定の時刻）を会話の状態へ保持する。
使用順でサブスクリプションが現在の種別より前にあれば、その時刻の後にClaude backendの`send_message`が始める最初のturnで同じ会話をサブスクリプションで再開し、なお上限なら既存の切替で再びAPIへ移る。
対象のturnは追送と、孫sessionの終了・利用上限・過負荷の後の自動再開であり、いずれも同じ`send_message`が開始するため判定を1か所に置く。
接続は所有タスクの起動時に決まるため、上限でAPIへ移した場合と同じ再開の処理で所有タスクを起動し直す。
CLIが自ら次のturnを始め得る待機（バックグラウンドタスクなど）の間は所有タスクを止めると作業を失うため、その待機の後にCLIが始めるturnは現在の接続で続き、次に`send_message`が始めるturnで戻す。

Claude Code 2.1.302の対話起動は、環境変数の未承認APIキーで「Detected a custom API key in your environment」の確認画面を表示する。
同版の本体は`customApiKeyResponses`による承認判定を持つ。
無人起動するAPIオーケストレーターはキーを環境変数へ直接置かず、所有者限定のsettingsファイルに`apiKeyHelper`を指定して選択したenvファイルから取得する。
キーの値をargvや登録簿へ保存せず、settingsファイルは子プロセス終了時に回収する。
親のAPI認証は環境変数と合成settingsの両方で隔離する。

サブスクリプション認証の検出は、`CLAUDE_CODE_OAUTH_TOKEN`、Linux・Windowsの設定ディレクトリにある`.credentials.json`のOAuth情報、macOSの`claude auth status`を使う。
macOSのKeychainエントリと`CLAUDE_CONFIG_DIR`の対応はCLI自身に解決させ、資格情報の値をログへ出力しない。
保存先は[認証の公式仕様](https://code.claude.com/docs/en/authentication#credential-management)、`authMethod`の判定値は[CLIの公式仕様](https://code.claude.com/docs/en/cli-reference#cli-commands)に従う。
接続先の保持とenvファイルを読む時機は[design-agents-server.md](design-agents-server-connections.md#起動候補と観測identity)にある。
