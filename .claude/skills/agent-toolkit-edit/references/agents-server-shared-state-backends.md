# agents_serverの共有状態と読み書きの担当：バックエンド固有の接続とAPI状態

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 共有状態の共通設計・実行主体・保存と回収の条件を判断する前 | `agents-server-shared-state.md` |
| セッション状態と内部フラグの参照範囲を選ぶ前 | `session-state-and-flags.md` |

## バックエンド固有の接続とAPI状態

保存済みCodex会話の再開では、`codex.py`が`thread/resume`の送信前に`hosts`索引を公開する。
子MCPの起動時に直接親を確定し、その親が起動した孫を復旧候補へ戻すためである。

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| Codexの獲得resetの論理消費と消費済みweekly窓 | `<状態ディレクトリ>/agents-server/codex-auto-reset/<account emailのSHA-256>.json`と同領域の`transaction.lock` | `codex_auto_reset.py` | 同じ状態ディレクトリを使うバックエンドが非ブロッキングのファイルロックで排他する。通信結果不明のUUIDは次の照会でも再利用し、成功済み窓への再消費を抑止する。資格情報やemail本文は保存しない |
| Codexのreset照会中フラグ`codex_auto_reset_pending` | `SessionState`のメモリーのみ | Managerの起動時可用性判定と自動再開監視 | Codexバックエンドが保留・照会開始時に設定し、照会終了・取消・接続解放時に解除する。照会中は旧結果の確定と自動継続を抑止し、成功後は既存の`usage_limit_resume_at`へ同一会話の再開を渡す。登録簿には保存しない |
| Claudeのplugin更新世代・未反映通知・監視thread | 所有タスクの`ClaudePluginReloader` | Claudeのセッション所有タスクのみ | `claude_plugins.py`がファイル変更をloopへ通知し、所有タスクがidle後にSDK制御要求を直列送信する。応答の集合・版・エラー数を検証した世代だけ反映済みにし、接続終了時に監視を停止・回収する |
| Codex会話の選択済みprovider ID、通常サブスクのprovider IDと検証済みの会話状態生成元`codex_state_provider`（provider IDの種別`credits`・`payg`は現在の`codex_credits_provider`・`codex_payg_provider`との一致で判定し、同じIDを両方へ設定することは拒否する。process-loopのCodex統括担当も選んだprovider IDを登録簿へ保存する） | `SessionState`、リソース解放時の`SessionResumeState`、および既存セッション登録簿内の内部再開情報 | Codexバックエンド、およびManagerの復元処理（公開用の状態、レスポンス、通知、結果の射影データからは読み取らない） | Codexバックエンドが接続先を確定し、新規開始時および互換性を確認した再開時のみ生成元を確定する。所有MCPサーバーはリソース解放済みの記録にもこれを保存し、既存の7日間掃引処理に委ねる。認証情報は保存しない |
| Claude会話の役割（`orchestrator`・`delegate`）、選択済みの接続（`credits`・`payg`・`subscription`・`inherit`。種別を区別する前の版の`api`は再開時にクレジットのenvファイルとの一致で`credits`か`payg`へ読み替える）とAPI接続のenvファイルの絶対パス | `SessionState`の`claude_role`・`claude_connection`・`claude_api_env_file`、`SessionResumeState`、およびセッション登録簿の内部再開情報 | Claudeバックエンド、Managerの復元処理、およびprocess-loop再開時の可用性判定 | 接続方式はClaudeバックエンドが起動時および切り替え時に確定し、所有MCPサーバーが保存する。process-loopのオーケストレーターも自身の役割と接続方式を登録する。新規セッションは`claude_orchestrator_connections`・`claude_worker_connections`の使用順に従って選択され、再開時には保存済みの接続方式を使用する（その種別を使用順から外した場合だけ使用順の先頭の利用可能な候補へ移す）。利用上限到達によってサブスクリプションからAPI接続へ切り替えた委譲先は、`claude_subscription_retry_at`をメモリー上でのみ保持し、指定時刻を過ぎた最初のプロンプト追送や自動再開のタイミングで、使用順でサブスクリプションが現在の種別より前にあればサブスクリプション接続へと戻す。接続設定を持たない環境で起動した`inherit`会話は、接続を管理しない間は親環境の設定と通常の認証情報を維持する。envファイルの値は各起動時に直接読み取るものとし、登録簿や公開状態へ値を複製・保存することはない |
| Claude起動別の合成settings | `<状態ディレクトリ>/<ルートsession識別子>/claude-settings/settings-<一意な値>.json`（ルート識別子を受け取らない直接構築時は`settings-<一意な値>`をルート名として使用） | 該当の起動におけるClaude CLI | Claudeバックエンドの`claude_settings.py`。親設定がファイルで与えられている場合や、API環境変数を合成する場合に、実行ユーザーのみ読み取り可能な起動別設定ファイルを生成する。各起動プロセスは、接続終了時・起動失敗時・依存確認の終了時に自身のファイルを削除・回収する |
| APIクレジットの残高不足の観測時刻（エンジン別） | MCPサーバーのメモリー（`_agents_server/api_credits.py`。`time.monotonic()`の値） | 新しい委譲先の使用順を決めるClaude・Codexバックエンド | 同じMCPサーバーのClaudeバックエンドが`credits`接続のturnの残高不足で、Codexバックエンドが`credits`候補の不受理で記録する。観測から1時間（`CREDIT_SHORTAGE_TTL_SECONDS`）以内に起動する同じエンジンの委譲先では、`credits`候補を使用順の後ろへ回す（候補からは外さない）。オーケストレーターの接続順は変えない。ファイルへ永続化せず、他のMCPサーバーとは共有しない。期限切れかMCPサーバーの再起動で通常の接続順に戻る。API残高は月1回の付与などで外部から回復するため、永続化せずに定期的に再確認するというユーザー要件による |
| engineの可用性を理由に除外した候補（解除待ちの対象のClaude利用上限は記録せず、旧版の理由`429`のClaude候補は読み込み時に除外の根拠から外す） | `<状態ディレクトリ>/unavailable-candidates.json` | 起動候補リストを解決するMCPサーバー | 当該の状態ディレクトリを共有する各MCPサーバー（ファイルロックを取得した上で読み書きを行う） |

| 対象 | モジュール |
| --- | --- |
| Claude起動時の設定合成と所有ファイルの回収 | `claude_settings.py` |
| 除外候補の記録管理 | `unavailable_candidates.py` |

Codex CLIは、stdio経由のMCPサーバーへ親環境の変数を限定して渡す。
渡されるのは、Codexの既定の許可リストにある変数、`env_vars`に列挙された変数のうち親環境に値が存在するもの、および`env`で明示された値に限られる。
外側の`agents_server`は、`thread/start`や`thread/resume`の`config.mcp_servers.agents_server`に対して、`env`経由でルートセッション識別子と書込主体識別子を渡し、`env_vars`経由で保存先・公開設定・プロキシ設定の環境変数名を渡す。
対象の変数名と除外条件は`agent_toolkit/_common/mcp_environment.py`で管理されている。
静的な配布設定には`environment_variable_names`を使い、内部MCPの開始・再開時には`runtime_environment_variable_names`を使う。
静的な変数集合には、認証・接続先・モデル設定、保存先、通信、ロケール、keyring接続などの公開名が含まれ、実行元の環境に関わらず共通して適用される。
公開設定である`AGENT_TOOLKIT_CONFIG_`から始まる変数名は、`_atk/config.py`の`config_environment_variable_names`が現行キーと旧キー（旧モデル設定と旧接続設定）の双方から取得して返す。
旧キーも子プロセスへ渡すことで、設定の拒否や移行先の案内の挙動を維持する。
動的な変数集合は、実際の親環境の全変数から、起動元の会話・端末・プロセスのコンテキストと上流で継承禁止とされた変数を除外して生成する。
除外判定は大文字・小文字を区別せずに行うが、単に名前にTOKENやKEYが含まれているという理由だけで認証用の変数を除外することはない。
miseの設定、信頼状態、インストール先、キャッシュの場所は、`MISE_CONFIG_DIR`、`MISE_STATE_DIR`、`MISE_DATA_DIR`、`MISE_CACHE_DIR`の公開変数名で引き継がれる。
これにより、XDG環境変数が変更された場合でも、呼び出し元が選択したmiseの配置先や信頼設定がそのまま維持される。
Codex向けプラグイン設定の生成処理（`pytools/_internal/codex_plugin_manifests.py`）も、この集合を`agents_server`と`pyfltr`の`env_vars`に反映する。
配布設定は変数名の一覧のみを保持するため、子プロセスに実際に渡るのは親環境で値が設定されている変数だけである。
なお、`env_vars`を持つのはCodex向けの設定のみであり、Claude向けの`.mcp.json`やAgent Plugins向けの`mcp.json`は従来の形式を維持する。
通常起動、再開、多段委譲のいずれの場合でも、呼び出し元が選択した設定、キュー、および状態の名前空間を引き継ぐ。
ただし、ownerや状態書込主体については、各起動時に明示された値を優先する。
外側の静的なMCP境界ですでに欠落した未知の変数は、内側から復元することはせず、親プロセスや認証ファイルを再読み込みして補うことも行わない。
`atk`自身のuv同期先はランチャー側で固定されているため、子プロセスには呼び出し元の`UV_PROJECT_ENVIRONMENT`の値（未設定の場合は未設定である状態）をそのまま戻す。
委譲先CLIや、process-loop・run-skillが起動する最上位の会話に対しては、起動元の会話・端末・プロセスのコンテキストを継承させない。
このコンテキスト除去処理は`_common/mcp_environment.py`の`strip_conversation_context`に集約されている。
MCPサーバーの環境整備や、`atk wi`のall-reposでのManager生成、`_atk/orchestrator.py`の`child_env`で使用される。
除去対象の名前は、内部MCPの`env_vars`と同じ`_NON_INHERITABLE_NAMES`で定義されており、すべてのバックエンドがこの整備済み環境を継承する。
ルート、書込主体、委譲元の記録情報、および引き継ぎ記録先のパスは、除去前の環境変数から確定し、各バックエンドが明示するownerや委譲フラグは除去後の環境に設定する。
起動区分を示すフラグ（`AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND`）も同様に除去後に設定する。
Claudeバックエンドでは全起動区分でSDKのenvに設定する。
CodexバックエンドではセッションごとのApp Serverの子環境に対して、開始時の起動区分と再開時の`SessionState.launch_kind`を設定する（モデル一覧や`thread/read`の問い合わせ用App Serverからは除外する）。
委譲先フックはこの値を参照して軽量起動かどうかを判別する。
Claudeの`inherit`接続の場合でも同様に会話コンテキストの除去を適用し、認証や接続設定は通常の継承ルールに従う。
これらの観測根拠は`docs/development/audit-records.md`の「.claude/skills/agent-toolkit-edit/references/agents-server-shared-state.md：実行主体：2026年10月18日」に記録されている。

API失敗状態は、すべてMCPサーバーの`SessionState`を基準（正）として管理し、同一の書込主体が状態ファイルへ反映（射影）する。
対象となるのは、Claudeの合成assistantメッセージや`SystemMessage(api_retry)`、Codexの`willRetry: true`による再試行中状態、ならびに過負荷（`serverOverloaded`）に伴う自動継続待ちの`api_error`である。
過負荷待機中の記録は、待機を予定したCodexバックエンドの終了処理が作成し、MCPサーバーが継続ターンを送信した時点で削除する。
待機の予定時刻や連鎖回数はMCPサーバーのメモリー内でのみ管理し、状態ファイルへは`api_error`の形式でのみ反映する。
なお、API失敗時の合成メッセージは最終アクティビティ時刻を更新せず、状態ファイルの変更通知のみを発行する。
`show`、`list`や各種CLIは共通のアクティビティ射影データから失敗の経過時間を算出し、その後の正常なassistantメッセージを受信した時点でエラー記録を削除する。
Claudeの`api_retry`では、`error`の種別、`error_status`、および受信時刻に`retry_delay_ms`を加算したUTCの`retry_at`を記録し、同一通知が繰り返された場合でも`attempt`の値を`count`として保持する。
Codexの再試行通知では、同一thread・ターン内の`codexErrorInfo`の種別と、存在する場合は`httpStatusCode`を記録し、後続のモデル応答やターンの終了時に削除する。
また`retry_at`も共通の公開項目として反映する。
生のエラーメッセージ本文は共有状態への保存対象から除く。

Claude CodeのWeekly limit（週間制限）や5時間上限の解除待ち状態についても、MCPサーバーの`SessionState`を正として管理する。
Claudeが報告した直近の利用可能枠（`usage_limit`）は、Claudeバックエンドが`RateLimitEvent`から取得して記録し、初期化前に届いた通知もセッション作成時に引き継ぐ。
ターンが待機対象の拒否によって終了した場合、Claudeバックエンドは結果を保留し、`api_error`に種別`usage_limit`、制限種別（`limit_type`）と解除予定時刻（`resets_at`）を記録する。
ターン内で発生した429の`api_retry`についても、直近の`RateLimitEvent`が待機対象の拒否を示していれば、同一の種別・制限種別・解除予定時刻を記録し、解除予定時刻が取得できなければ`retry_at`を用いる。
その後に発生した429以外の再試行は、通常のAPI再試行として処理する。
次回継続の実行時刻と待機回数はメモリー内でのみ管理する。
MCPサーバーの常駐監視処理が予定時刻に該当セッションへ継続リクエストを送信し、正常なモデル出力が得られた時点で利用制限の記録と`api_error`を削除する。
保留中の結果は`auto_resume_deadline`や保持期限の適用対象から除外される。
状態ファイル、`show`、`list`、ならびに各種CLIに対しては、`limit_type`と`resets_at`を`api_error`の公開項目として反映する。
利用上限の解除を待機するかどうかの判定は`_atk/usage_limit_policy.py`の`should_wait`が行い、エンジンごとの設定である`claude_usage_limit_wait`と`codex_usage_limit_wait`を参照する。
既定値の`five-hour`は5時間枠のみ待機し、`always`は週次枠なども待機対象とし、`never`は待機しない。
Claudeからの途中通知についても待機や代替接続への切り替えを評価し、利用枠情報が通知内に含まれていない場合に限り、同一会話・同一起動後の元ログと突き合わせる。
一般的な429や529の一時的エラー記録は、その後の正常なassistant応答によって解除される。
委譲先の接続候補順は`claude_worker_connections`（既定`credits,subscription`）、オーケストレーターは`claude_orchestrator_connections`（既定`subscription,credits`）の使用順のうち認証のある種別とする。
`claude` CLIで起動するprocess-loopのオーケストレーターは`credits`を除く。
4キーのどれも設定しない場合は`inherit`を維持する。
候補順に後続の接続手段が残っている場合は、利用上限への到達またはAPIクレジット不足を検知したタイミングで処理結果を保留し、常駐監視処理が`claude_api_resume_pending`を検知して同一セッションを後続の接続手段で再開する。
この再開予定はメモリー内のみに保持し、選択された接続方式とAPI環境ファイルのパスを既存の内部再開情報へ保存する。
切り替え後の会話は、その後の設定変更・解除やリソース解放を経ても保持された接続方式を継続して使用し、新規セッションは常に候補順の先頭から開始する。
保持した種別をロールの使用順から外した場合は、次回ターンや再開時に同一会話を使用順の先頭の利用可能な候補へ移行させる。
利用上限によってサブスクリプションから切り替えられた委譲先に限り、使用順でサブスクリプションが現在の種別より前にあれば、解除予定時刻を過ぎた最初のターン（プロンプト追送や自動再開）で同一会話をサブスクリプションへ戻す。
なお、CLIが自律的に次のターンを開始する可能性がある待機状態（バックグラウンドタスク実行中など）においては、実行中のタスクを中断しない。
リソース解放後は復帰すべき時刻情報が失われるため、保存されていた接続方式をそのまま使い続ける。
接続切り替えのログにはセッションIDと利用制限の種類のみを記録し、環境変数の値や切り替えの詳細情報を公開レスポンスや継続指示プロンプトに含めることはしない。

Codexの接続先プロバイダーの優先順位は`codex_orchestrator_connections`・`codex_worker_connections`の使用順と`codex_credits_provider`・`codex_payg_provider`で管理する。
委譲先でAPIが受理されなかった場合やサブスクリプションの利用上限に達して使用順の次の候補へ切り替える際も、Claudeと同様の結果保留と常駐監視の仕組みを利用する。
統括担当（`orchestrate`）は会話の中で切り替えない。
4キーのどれも設定しない会話は通常のCodex設定に従う。
選択されたプロバイダー情報は同一会話の再開処理へ引き継がれ、主接続先の設定変更や利用枠の回復は新規起動に対してのみ適用される。
後続候補の選択は、次回の代替試行時に現在の候補リストから順に行われる。
現在のアカウントの利用可否は永続ファイルには記録せず、新規起動の都度App Serverへ問い合わせて確認する。
ユーザー向けのログには候補の診断情報のみを記録する。
ユーザー向けの設定方法、料金、認証、トラブル復旧手順は`docs/guide/codex-guide.md`に記載されている。
cold resume（完全停止からの再開）の内部設計については`docs/development/design-agents-server.md`を参照のこと。
停止状態からの再開前には、`config/read`を実行して有効なプロバイダーを特定する。
Codexバックエンドは、接続切り替え時および過去ログからの再開時に、`thread/read.path`または既存ログの解決から得られるrollout情報を検証して互換性を確認する。
検証対象となる項目は、`session_meta.id`、provider、`response_item`内のcompaction・`encrypted_content`、ならびに`compacted`内の`replacement_history`・`compaction_response_id`である。
暗号化等の不透明なデータ値の内容自体は解釈の対象外とする。
生成元が異なるか判別できない不透明なデータが含まれる場合、あるいはログの欠落や不正が認められる場合は、`threadStateIncompatible`エラーでセッションを終了し、元のログと進捗を保全した上で引き継ぎ手順を案内する。
検証を通過した同一プロバイダーへの通常再開には`codex_state_provider`を使用する。
生成元を示す識別子が存在しない古い移行ログにおいて、選択された接続先と初期`session_meta`のプロバイダーが異なり、かつ不透明なデータが存在する場合は、現在の選択先への再開は行わず、引き継ぎ手順を案内する。

モデル候補の設定は、`atk config get config_dir`が返すディレクトリ内の`config.json`に保存されており、MCPサーバーは起動するたびにこれを読み込む。
古い9段階キーや用途別・エンジン優先順の5キーが保存されている場合は、候補解決または`atk config show` / `get`を最初に実行した主体（MCPサーバーまたは`atk` CLI）が、これらを6工程の候補リストへと合成し、廃止された旧キーを除去して単一のファイルを一括で更新する。
移行結果は保存されている値のみから一意に決定されるため、複数のプロセスが同時に移行処理を実行しても同一の内容となる。
移行の詳しい仕様は`agent-toolkit:delegation`の`references/process-model-settings.md`「工程別モデル設定」を参照のこと。

明示的なモデル候補リストから選択されたAPI会話は、API直接接続およびサブスクリプションからの移行のいずれであっても、`stop`の実行や保持期限切れに伴うリソース解放が行われた後、解放済みレコードに保存されている内部再開情報を基に復元される。
