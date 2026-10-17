# agents_serverの共有状態と読み書きの担当

`agents_server`の状態はMCPサーバーのメモリー、状態ディレクトリのファイル、フックが記録するセッション状態およびstatuslineが読む射影の4つの表現に分かれる。
実行主体ごとに更新できる範囲が異なるため、挙動を確定する際は関係する全ての処理の流れを読む（単一の処理だけを読むと、別の処理が同じ状態を更新しない事実を見逃す）。

## 実行主体

| 実行主体 | 実体 | 寿命 |
| --- | --- | --- |
| MCPサーバー | 起動スクリプト`agent-toolkit/agent_toolkit/agents_server_mcp.py`。MCPツールは`_agents_server/mcp_tools.py`、sessionの管理は`_agents_server/manager.py`と`manager_*.py`が持つ | ホストがMCPサーバーを起動する単位ごとに1プロセス。起動時の`CLAUDE_CODE_SESSION_ID`を保持し続ける。各プロセスが保持するsessionの集合は独立する |
| `atk`のCLI | `atk agents wait`、`atk agents notify`、`atk agents list`、`atk agents show`、`atk agents logs`、`atk lane create/integrate/delete` | 呼び出しごとの短命プロセス。現行のsession識別子を得る。レーン操作は既存のsession登録を読むだけとする |
| フック | `agent-toolkit/agent_toolkit/_hooks/posttooluse.py`。観測の記録の書き込みと読み取りは`_hooks/agents_server_observations.py`が持つ | イベントごとの短命プロセス。入力JSONで現行のsession識別子を得る |
| statusline | `rust/claude-statusline` | 描画ごとの短命プロセス。入力JSONで現行のsession識別子を得る |

`CLAUDE_CODE_SESSION_ID`は子プロセスの起動時に現行のsession識別子が注入される値である。
Claude Codeが同一プロセスのままsession識別子を切り替えた場合、切替後に起動する短命プロセスは新しい値を得る一方、長命なMCPサーバーは起動時の値を保持し続ける。
このため`start`の`task`が起動本文へ添える委譲元の記録情報（`atk-auto`の`kind="launcher-record"`。`ManagerBase.launcher_record_context`が組み立てる）も、会話の切替後は古い会話を指し得る。消費側は明示された出所と取得済みの`record`・`line`を優先し、対象発話との一致を確かめる。
MCPサーバープロセスには`CLAUDE_PID`が渡らないため、Claude Codeプロセスを指す識別子を環境変数から解決できない。

Codex CLIはstdio MCPサーバーへ親環境を限定して渡す。届くのはCodexの既定の許可リストにある変数、`env_vars`に列挙した名前のうち親環境に値がある変数、`env`で明示した値である。
外側の`agents_server`は`thread/start`・`thread/resume`の`config.mcp_servers.agents_server`へ、`env`でルートsession識別子と書込主体識別子を配送し、`env_vars`で保存先・公開設定・proxy設定の変数名を渡す。
変数名と除外条件は`agent_toolkit/_common/mcp_environment.py`が持つ。静的な配布設定は`environment_variable_names`、内側MCPの開始・再開は`runtime_environment_variable_names`を使う。
静的な集合は認証・接続先・モデル設定、保存先、通信、ロケール、keyring接続などの公開名を含み、生成元の環境には依存しない。
公開設定の`AGENT_TOOLKIT_CONFIG_`名は`_atk/config.py`の`config_environment_variable_names`が現行キーと旧キーから返す。旧キーも子へ配送して拒否と置換案内を保つ。
動的な集合は実際の親環境の全名前から起動元の会話・端末・プロセスのcontextと上流の継承禁止名を除く。除外は大文字・小文字を区別せず、TOKEN・KEYという名前だけで認証名を除外しない。
miseの設定・信頼状態・導入先・キャッシュの所在は`MISE_CONFIG_DIR`・`MISE_STATE_DIR`・`MISE_DATA_DIR`・`MISE_CACHE_DIR`の公開名で引き継ぎ、XDGを変更しても呼出元が選んだmiseの所在と信頼設定を保つ。
Codex向けプラグイン設定の生成器（`pytools/_internal/codex_plugin_manifests.py`）も、同じ集合を`agents_server`と`pyfltr`の`env_vars`へ射影する。
配布設定は変数名だけを持ち、子へ届くのは親環境に値がある変数に限られる。`env_vars`はCodex向けの設定だけが持ち、Claude向けの`.mcp.json`とAgent Plugins向け`mcp.json`は従来の形式を保つ。
通常起動・再開・多段委譲でも、呼出元が選んだ設定、キューと状態の名前空間を引き継ぐ。owner・状態書込主体は各起動が明示した値を優先する。
外側の静的なMCP境界ですでに失われた未知名は内側から回復せず、親プロセスや認証ファイルの読直しで補わない。
atk自身のuv同期先はlauncherが固定し、子へは呼出元の`UV_PROJECT_ENVIRONMENT`の値と未設定の別を戻す。
MCPサーバーが起動するCodex App Server・Claude CLI・Antigravity CLIは親環境をコピーするため、この境界で渡した値を引き継ぐ。
配送するルートはManagerが持つ`StatusFileWriter.root_session_id`である。環境にownerが無くManagerが生成したルートも、Codex App Serverの子環境、threadの開始・再開のMCP環境とhost索引、Claude SDKの新規・再開環境、Antigravityの子環境へ同じ入力として渡す。backendはこの入力から通知先の所有者を決める。Antigravityではルート配送だけを保証し、固有の通知元識別子の解決と通知成功は別の課題として扱う。計画の所有記録とUWIの投入元の解決はこの配送と別の契約であり、Managerが生成したプロセス専用のroot（`mcp-`で始まる識別子）は会話を表さないため、所有会話と投入元の記録から除く。判定は`agent-toolkit/agent_toolkit/_plan/owner_records.py`の`resolve_conversation_session_id`が持つ。
CodexのPostToolUseフックは、所有session識別子があり環境変数から書込主体を解決できない場合、入力JSONの検証済み現行session識別子を`AGENT_TOOLKIT_STATUS_HOST_SESSION`相当として補完する。PostToolUseフックと`atk agents wait`は`hosts`索引が存在する場合は委譲元のthreadから書込主体を逆引きし、状態ファイルと待機対象登録を同じ名前空間で扱う。索引が無い場合は補完した識別子をそのまま書込主体として使う。
Codex backendは、子sessionを起動した委譲先から`atk agents wait`に成功した`commandExecution`を受け取る。
人の端末の標準出力、またはエージェント環境で量によらず自動保存した`stdout`の`保存先:`が示す保存結果の終端識別子を、`_agents_server/wait_output_tracking.py`の`consume_agents_wait_output`で追跡集合から外す。
標準エラーの保存先は別の標識で返し、待機のJSON Linesとして読まない。保存先は結果回収より先に開き、準備できなければ結果を消費しない。
待機失敗と読取不能の結果は追跡集合へ残す。

Codex App Serverの所有単位は委譲sessionである。Codex backendはsessionごとにApp Serverを起動し、そのsessionの開始・継続・再開・中断を所有する接続へ送る。サーバー要求への応答と接続障害の反映も、その接続を所有するsessionだけを対象にする。
App Serverはthreadごとのstdio MCPサーバーを子孫として起動し、`thread/unsubscribe`の応答後も猶予の間は生存させる（2026年10月16日にCodex CLI 0.163.0で観測）。このため購読解除の応答は資源解放の完了を表さず、完了の判定にはApp Serverと子孫の終了を使う。
sessionの資源解放（`stop`、保持期限の経過、起動候補の断念、MCPサーバーの停止）は、所有するApp Serverと、App Serverへの終了要求の直前に列挙した子孫の終了をOS上で確認して完了とする（App Serverの終了後は子孫の親が変わり、PIDから辿れなくなるため）。終了を確認できない対象が残れば、接続の所有を保ったまま再試行できる失敗を返し、登録簿は解放前の記録のまま残す。
解放後の`send_message`は新しいApp Serverで`thread/resume`して同じ会話を再開する。モデル一覧と`thread/read`の照会は専用のApp Serverを起動し、応答の後に終了させる。

## 共有状態ごとの保持先と読み書きの担当

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| session一覧と`status`・`progress` | MCPサーバーのメモリーの`SessionState` | MCPサーバー | managerと、当該sessionのengineを所有するbackend。各backendは共有一覧から自身のengineだけを検索・状態遷移の対象とし、接続障害時も他engineを変更しない |
| Codex会話の選択済みprovider IDと通常サブスクのprovider ID | `SessionState`、解放時の`SessionResumeState`と既存session登録簿の内部再開情報 | Codex backendとmanagerの復元処理。公開の状態・応答・通知・結果の射影は読まない | Codex backendが接続先を確定し、所有MCPサーバーが既存登録簿へ保存する。認証情報は保存しない |
| Claude会話の役割（`orchestrator`・`delegate`）、選択済みの接続（`api`・`subscription`）とAPI接続のenvファイルの絶対パス | `SessionState`の`claude_role`・`claude_connection`・`claude_api_env_file`、`SessionResumeState`とsession登録簿の内部再開情報 | Claude backend、managerの復元処理とprocess-loopの再開時の可用性判定 | 接続はClaude backendが起動時と接続の切替時に確定し、所有MCPサーバーが保存する。process-loopが直接起動するオーケストレーターは`process_loop_session.py`が自身の会話の役割と接続を登録する。再開は保存した接続を使い、新規sessionは`claude_api_mode`の候補順の先頭から選ぶ。envファイルの値は各起動で読み、登録簿と公開状態へ複製しない |
| 引き継ぐ用途・配送済み追送・各turnの最終応答全文と終端エラー | `SessionState`、`SessionResumeState`とsession登録簿の`LaunchInfo`。解放済み記録では内部の`handoff_info` | managerの引き継ぎ起動と再開処理 | 所有MCPサーバーが起動・追送・終端時に更新し、解放時も既存登録簿へ保持する |
| statusline・CLI向けの状態ファイル | `<状態ディレクトリ>/<ルートsession識別子>/<書込主体>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show`、同じルートに属する他のMCPサーバー（稼働中の子孫を持つ終端sessionの表示判定）、全ルートを読む資源記録 | そのルートに属する各MCPサーバー。各MCPサーバーは自身のファイルだけを書く |
| 書込主体からホストsessionへの索引 | `<状態ディレクトリ>/<ルートsession識別子>/hosts/<書込主体>.json` | 状態ファイルの`host_session_id`を委譲元のsession識別子へ解決する主体、PostToolUseフック、`atk agents wait` | そのsessionを起動したMCPサーバー |
| 状態ファイルの`host_session_id`（そのファイルが載せるsessionの委譲元） | 状態ファイル | statuslineと`atk agents list`の人向けツリー（親子付けの親IDとして使う。`status_reader.load_root`が各sessionへ付ける）、同じルートに属する他のMCPサーバー（稼働中の子孫の判定） | その状態ファイルを書き込むMCPサーバー。状態ファイル名（`owner_status_file`）は書込主体の識別子であり、結果の所有権の判定に使い、親IDへ転用しない |
| 状態ファイルの生存の印`heartbeat_at` | 状態ファイルを書き込むMCPサーバー | statusline、同じルートに属する他のMCPサーバー | その状態ファイルを書き込むMCPサーバー |
| 終端結果と回収済み判定 | `<状態ディレクトリ>/<ルートsession識別子>/results/<session_id>.json`の存在。CLIが回収途中の結果は下記のrun別退避物へ移る | MCPサーバー、`atk agents wait`、statusline | MCPサーバー（作成と削除）、待機CLI（自身の書込主体が公開した結果だけを削除） |
| CLI待機の所有権 | `<状態ディレクトリ>/<ルートsession識別子>/wait-locks/<書込主体>.lock`のファイルロック | `atk agents wait`、Stop時の未観測作業の助言 | `atk agents wait`。1書込主体につき同時に1実行だけが全対象を待ち、ロックファイル自体は解放後も保持する |
| CLI待機の実行結果 | `<状態ディレクトリ>/<ルートsession識別子>/wait-results/<書込主体>/<run_id>.json`と`current.json` | 先行waitへ合流する後続`atk agents wait`、待機対象0件（終了コード10）と`consumed`（終了コード8）の診断を組み立てる後続`atk agents wait` | 先行waitが`running`と`published`および再待機が必要かを示す`continuable`を書き、結果を返した後続waitが`consumed`を書く |
| CLI待機が回収途中の結果と通知 | `<状態ディレクトリ>/<ルートsession識別子>/wait-results/<書込主体>/<run_id>/results/<session_id>.json`と`notices/<通知ファイル>` | 中断後に同じrunを再開する`atk agents wait` | 待機CLI。結果は所有者を確認した排他区間で、通知は原本の削除前に退避する |
| CLI待機の対象登録 | `<状態ディレクトリ>/<ルートsession識別子>/wait-targets/<書込主体>/<session_id>.json` | `atk agents wait`、Stop時の未観測作業の助言 | PostToolUseフック（子sessionの開始とreply再開時の追加。`atk agents wait`と同じく索引を経たルートへ登録する）、`atk agents wait`（待機開始時の追加と回収・破棄時の削除）。助言側は読むだけとする |
| 全sessionの終端登録と再開情報、所有するagents_serverとturnを実行する委譲先CLIのプロセスの識別（pidと起動時刻）、所有側による解放済みの理由と時刻、作成時点の委譲元（`launcher_session_id`） | `<状態ディレクトリ>/sessions/<session_id>.json` | 親を所有するMCPサーバー、同じ識別子を再解決するMCPサーバー、保持しない識別子への応答を診断するMCPサーバー、`atk agents wait`、`atk agents show`（状態ファイルに無いsessionの実行中・終端）、`atk serve`のセッション一覧（ローカルとリモートヘルパー。委譲元だけを親子付けに読む）、`atk lane create/delete`（専用worktreeを使う担当のcwdと、`session_registry.resolve`の解決結果から終了を判定する）。いずれも`session_registry.resolve`で解決し、実行プロセスの不在による中断を同じく扱う | そのsessionを所有するMCPサーバー（停止時の終端公開と、委譲先CLIを起動し直した時点の識別の更新を含む）。所有者のいない`running`の記録への終端の公開だけは、再起動後に同じ識別子を照会したMCPサーバーも行う（下記「再起動をまたぐsessionの解決」の条件） |
| 終端済み委譲sessionの解放要求と応答 | `<状態ディレクトリ>/sessions/<uuid>.release-request`と同名の`.release-response` | lane回収CLIと対象sessionを所有するMCPサーバー | CLIが要求を作成し、所有MCPサーバーが終端と所有を再検収して結果保持のstopを実行し応答する。CLI完了時に両ファイルを回収する |
| Codexコンパクションの計測記録 | `<状態ディレクトリ>/compaction/<thread_id>.jsonl` | `atk run-script session-review-evidence` | agents_serverのCodex backend |
| 上り通知 | `<状態ディレクトリ>/<ルートsession識別子>/notices/<通知ファイル>` | MCPサーバー、`atk agents wait` | `atk agents notify`が作成し、MCP待機とCLI待機が回収時に削除する |
| ルートsession識別子の索引 | `<状態ディレクトリ>/aliases/<現行のsession識別子>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | PostToolUseフック（`start`、`send_message`、`list`など、`root_session_id`を明示する応答の値を操作名によらず使う） |
| MCPツールの呼び出し記録 | セッション状態の`agents_server_sessions` | PostToolUseフックとStop時の助言 | PostToolUseフック |
| 委譲先CLI自身の診断ログ | `<診断ログのディレクトリ>/delegate-debug/<起動時刻>-<session識別子>-<起動区分>.log` | 初期化失敗を事後に調べる主体 | Claude backend（作成、初期化完了後の改名と、保持世代を超えた記録の削除） |
| Claude起動別の合成settings | `<状態ディレクトリ>/<ルートsession識別子>/claude-settings/settings-<一意な値>.json`。rootを受領しない直接構築では`settings-<一意な値>`をrootとして使う | 当該起動のClaude CLI | Claude backendの`claude_settings.py`。親設定がファイルの場合とAPI envを合成する場合に、所有者のみ読める起動別ファイルを作成する。各起動は自分のファイルだけを接続終了・起動失敗・依存確認終了で回収する |
| Antigravityの公開イベントログ | `<状態ディレクトリ>/<ルートsession識別子>/logs/<session_id>.jsonl` | `atk agents logs`、`atk run-script session-review-evidence`、セッションのイベント経過を調べる主体 | Antigravity backend（公開stream-jsonイベントの追記） |
| engineの可用性を理由に除外した候補（解除待ちの対象のClaude利用上限は記録せず、旧版の理由`429`のClaude候補は読み込み時に除外の根拠から外す） | `<状態ディレクトリ>/unavailable-candidates.json` | 起動の候補列を解決するMCPサーバー | その状態ディレクトリを共有する各MCPサーバー（ファイルロック下の読み書き） |

process-loop、レーン操作と配布物保持が共有する状態は、前表のagents-server領域と同じ`state_dir`の下で次のように保持する。

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| ホストsessionのターンと子エージェントの活動 | `session-activity/<session_id>.json` | `_common/session_activity.py`。実プロセス、セッション記録、共通親子解決と合わせて開始猶予を判定する | `hook.py`の活動記録。排他下で最新状態を置換し、SessionEndで自sessionを回収する。残存記録はPIDと起動時刻で失効を判定する |
| WI投入作業の投入先と作業期間 | `session-activity/submissions/<session_id>.json` | `_common/session_activity.py`。投入先がprocess-loopの対象と交わる会話とその子孫だけを開始猶予の理由にする | `atk wi submission begin`が作成・置換し、`end`が削除する。SessionEndでは削除しない |
| origin単位の処理所有と未完了の再開情報 | `process-loop-ownership/<originのSHA-256>.json`と同名lock | 専用process-loopとall-repos | `_atk/wi/process_loop_ownership.py`。短い排他で所有tokenと生存を検収し、worktree・Git共通ディレクトリ・担当session・レーン所有sessionを更新する |
| 対象別の担当返却 | `process-loop-ownership/<originのSHA-256>.result.json` | all-reposの中継と再開を担う処理 | `_atk/wi/process_loop_all_repos.py`。担当終端を観測した後に確認済み工程と残工程を保存する |
| Git統合と計画保存・WI終端の進行 | `lanes/<所有session>/<lane>.json`のintegration | `atk lane integrate`と回収判定 | `_atk/lane.py`。Git統合と成功済みの最終化を保存し、失敗後は残工程だけを再開する。全最終化と選定集合・統合状態の整合確認の成功後に統合済みを記録する。rebase対応表はmanaged-tempに保存し、レーン回収後も同じ保持期限で参照できる |
| 保持した配布物を使用するプロセス | `plugin-root-leases/<保持領域の識別子>-<pid>.json`と領域ごとの`.lock` | 配布物の起動側とmanaged-tempの回収・掃引 | `_common/plugin_root_leases.py`。起動側が領域path・PID・プロセス作成時刻を登録し、読む側は同じ領域の排他下で生存を検収する。終了済みの使用記録を除き、取得不能な記録と使用中の実体を保護する。使用登録と回収が排他を共有するのは、検収と登録の間に実体を削除しないためであり、`atk`の自動掃引だけは排他を待たず、競合した領域を保持して次回へ回す |
| 同じ配布元・版から共有する完成済み配布物 | managed-tempの保持領域直下の`.agent-toolkit-plugin-root.json` | `_agents_server/plugin_root.py`の起動・再利用 | コピーする起動側が配布元・版・root名を保存する。内側MCPも同じ完成済み実体を使い、回収側は前行の使用記録を検収する |
| セッション配下の作業用領域の回収再開情報 | `managed-temp/`配下の`.child-cleanup`ファイル | `atk managed-temp cleanup` | `_atk/managed_temp/inventory.py`。親の登録と対象identityを検収して回収し、途中失敗の情報を次回の同じ操作へ渡す。保存証拠を持つ親領域を保持する |

使用記録のない保持領域の回収では、`_common/plugin_root_leases.py`がOSの起動引数と開いている既存診断ログへの参照も読む。旧形式は同じOSアカウント・状態名前空間のMCPと帰属不明の候補を保持し、候補の不在を確認した後に回収へ進める。旧形式・新形式とも、lease登録前に保持先を直接参照する公開MCPランチャーを保護する。新規の作成には`agents-server-plugin-root-shared`のprefixを使い、旧形式の列挙互換と完成済みmarkerの契約を保つ。

`_common/session_parents.py`と`_common/session_activity.py`は前表のsession登録簿を読み、保存済みの委譲元とプロセス識別を活動判定に使う。
MCPサーバーのSessionStateとsession登録簿の更新は、両モジュールの対象外である。

状態ディレクトリ配下の読み書きは、`agent-toolkit/agent_toolkit/_agents_server/`配下の次のモジュールが担う。

| 対象 | モジュール |
| --- | --- |
| 配置とsession識別子の検証 | `shared_layout.py` |
| Claude起動の設定合成と所有ファイルの回収 | `claude_settings.py` |
| ルートと書込主体の識別、索引（`aliases`・`hosts`） | `shared_roots.py` |
| statusline・CLI向けの状態ファイルの出力 | `status_file.py` |
| 一覧と資源記録の共有読取・生存判定・session重複排除 | `status_reader.py` |
| 開始・終端の稼働数とホスト資源の診断記録 | `resource_snapshot.py` |
| 終端結果の保持と回収 | `retained_results.py` |
| CLI待機の対象登録 | `wait_targets.py` |
| 上り通知の回収 | `notice_inbox.py` |
| 除外した候補の記録 | `unavailable_candidates.py` |
| 保持期限を過ぎた状態の掃引 | `shared_sweep.py` |
| 全sessionの終端登録と、実行プロセスの不在による中断の解決 | `../_common/session_registry.py`（`atk lane`も読むため、層の順序で前にある`_common`へ置く） |
| 終端済みsessionの解放要求と応答、媒体の回収 | `../_common/session_release.py`（回収CLIと所有MCPサーバーが共有する） |
| 前任の記録抽出と後任へ渡す引き継ぎ本文 | `session_handoff.py`（既存session-reviewの会話抽出を使う） |

sessionの`created_at`は最初の開始時刻で、turnごとに更新する`started_at`と別に保持する。`label`と`prompt`も`start`の時点で決まる。これら3項目を再開と再起動をまたいで保持する起動情報とし、項目の集合は`agent-toolkit/agent_toolkit/_common/session_registry.py`の`LaunchInfo`が1か所で定める。MCPサーバーのメモリーを基準とし、状態ファイルとsession登録簿（版数2の任意項目）へ射影する。再開したsessionは登録簿または退避した再開情報の値を`LaunchInfo`の項目ごとに引き継ぐ。項目を持たない旧形式の登録簿から再開した場合、`label`と`prompt`は空文字列とし、`created_at`は再開時刻から数え直す。
`start`と`send_message`の公開応答へは、開始したsessionと配送先のsessionがメモリーに保持する`label`を返す。委譲元が起動応答と追送応答だけで`session_id`と担当名を対応付け、追送の宛先を確かめられるようにするためである。
`LaunchInfo`は用途の`usage`、配送済みの`followup_prompts`、turn番号と最終応答全文の`final_responses`、`terminal_error`も保持する。
これらは所有側が起動・追送・終端の各契機で更新し、同じsessionの再開と前任からの引き継ぎに使う。
`handoff_from`を使うstartは前任のcwdとmodel_typeを復元して応答へ返し、PostToolUseフックは引き継ぎ起動時だけ両値を応答から取得し、後任の待機対象と工程種別を記録する。通常のstartでは入力cwdとmodel_typeを検収する。
引き継ぎ用の会話抽出はsession-reviewの`--conversation-only`を使う。既存bundleの抽出物を生成し、候補文書・比較材料・統計の生成とfailure ledgerの更新を省く。生成物の保存先はmanaged-tempの保持期限へ委ねる。

ClaudeのAPI失敗が連続する間と、Codexの過負荷（`serverOverloaded`）による自動継続を待つ間の`api_error`はいずれもMCPサーバーの`SessionState`を正とし、同じ書込主体が状態ファイルへ射影する。過負荷の待機中の記録は待機を予定したCodex backendの終端処理が作成し、MCPサーバーが継続のturnを送った時点で削除する。待機の予定時刻と連鎖の回数もMCPサーバーのメモリーだけに置き、状態ファイルへは`api_error`の形でだけ射影する。API失敗の合成メッセージは活動時刻を進めず、状態ファイルの更新だけを通知する。`show`・`list`とCLIは共通の活動射影から失敗の経過を算出し、正常なassistantメッセージで記録を削除する。生のエラー本文は共有状態へ保存しない。

委譲sessionの文脈使用量（`context_tokens`と`context_window`）もMCPサーバーの`SessionState`を正とし、同じ書込主体が状態ファイルへ任意の数値として射影する。値は累計課金量ではなく直近のモデル文脈の観測値であり、当該sessionのengineを所有するbackendが`_agents_server/context_usage.py`の`record_context_usage`で置き換える。
情報源はengineごとに次のとおりである。

- Claude: SDKの`get_context_usage()`の`totalTokens`と`rawMaxTokens`
- Codex: 同じthread・turnの`thread/tokenUsage/updated`の`last.totalTokens`と`modelContextWindow`
- Antigravity: 最新の`agent_response` stepの`total_tokens`と`cache_read_tokens`の和（上限は持たない）

有限かつ非負の数値だけを受理し、未知の値は項目ごと省く。この更新は活動時刻を進めず、表示更新の通知だけを送る。終端後の再開情報（`SessionResumeState`）は表示のため最後の観測値を保持し、値は同じsessionの表示だけに使う。
statuslineと`atk agents list`は`subagent.rs`の`format_tokens`と`context_usage.format_tokens`で同じ書式（小数1桁の`k`と整数の`%`）に描く。Rustのテストとリポジトリrootの`claude_statusline_invariant_test.py`が`rust/claude-statusline/testdata/agents_server_state.json`の同じ数値例を読む。

Claude CodeのWeekly limitと5時間の利用上限の解除待ちもMCPサーバーの`SessionState`を正とする。Claudeが報告した最後の利用枠（`usage_limit`）はClaude backendが`RateLimitEvent`から記録し、初期化より前に届いた報告もsessionの作成時に引き継ぐ。turnが待機対象の拒否で終わると、Claude backendが結果を保留し、`api_error`へ種別`usage_limit`、利用枠の種類（`limit_type`）と解除予定時刻（`resets_at`）を記録する。次の継続の時刻と待機の回数はメモリーだけに置く。MCPサーバーの常駐監視が予定時刻に同じsessionへ継続を送り、正常なモデル出力で利用枠の記録と`api_error`を削除する。保留中の結果は`auto_resume_deadline`と保持期限の対象にしない。状態ファイルと`show`・`list`・CLIへは`limit_type`と`resets_at`を`api_error`の公開項目として射影する。
委譲先の候補順は、`claude_api_mode`が`pay-as-you-go`ならサブスクリプション、APIの順、`api-credits`ならAPI、サブスクリプションの順とする。
候補順に後続の接続が残る場合は、同じ利用上限かAPIクレジットの不足の契機で結果を保留し、`claude_api_resume_pending`を常駐監視が消費して同じsessionを後続の接続で再開する。
この予定はメモリーだけに置き、選択した接続とAPI接続のパスを既存の内部再開情報へ保存する。移行後の会話は設定変更・解除や資源解放の後も保持した接続を使い、新規sessionは候補順の先頭から始める。
切替のログはsession IDと利用枠の種類だけを記録し、envの値や切替の情報を公開応答と継続指示へ加えない。

Codexの接続先選択順は`codex_model_providers`が持つ。先頭APIの不受理やサブスク利用上限で後続APIへ移す間も、既存の結果保留と監視を使う。空設定の会話は通常のCodex設定に従う。選択済みproviderは同じ会話の再開へ渡し、主接続先の設定変更と利用枠の回復は新規起動だけへ適用し、後続候補は次の代替試行で現行列から選ぶ。現在アカウントの利用可否は永続台帳へ記録せず、新規起動ごとにApp Serverへ照会する。人向けログへは候補の診断だけを記録する。接続先の設定とcold resumeの契約は`docs/guide/codex-guide.md`と`docs/development/design-agents-server.md`にある。

モデル候補の設定は`atk config get config_dir`が返すディレクトリ配下の`config.json`が持ち、MCPサーバーは起動ごとに読む。旧モデル設定（`high_tier_model`など4キー）が保存されている場合は、候補解決か`atk config show`・`get`を最初に行った主体（MCPサーバーまたは`atk`のCLI）が同じファイルを一括移行で書き換える。移行結果は保存値だけから決まるため、複数の主体が同時に移行しても同じ内容になる。

状態ディレクトリは`atk config get state_dir`が返すディレクトリ配下の`agents-server`とする。
レーン資源の所有登録と回収結果への参照は、同じ`state_dir`配下の`lanes/<所有session>/`へ別に置く。`_atk/lane.py`が作成・明示再利用・回収を所有し、通常終了とprocess-loopの子終了後も同じ処理を使う。レーン管理はsession登録簿を`_common/session_registry.py`の`resolve`の解決結果（実行中かとcwd）で読むだけとし、状態と終端登録の更新はMCPサーバーが所有する。回収結果は専用worktreeの外のmanaged-tempへ保存し、その既存保持期限へ委ねる。未解決の資源登録は保持し、回収処理が除くのは解決済みのレーン登録だけとする。
内部の保存項目と公開応答の項目は別に扱う。活動は`seconds_since_activity`、API失敗は種別・HTTP状態・経過時間（利用上限の解除待ちでは種類と解除予定時刻も）を公開し、集計回数と初回時刻は内部の保持に使う。CLI一覧は識別子・状態・ラベル・直近操作・結果保留・開始時刻・文脈使用量と活動診断へ限定する。MCPの通常応答には絶対活動時刻を載せず、`show(verbose=True)`で更新・出力時刻と実行条件を示す。結果のturn番号と確定時刻、通知の送信時刻は保存と整列に残し、回収する公開応答から除く。用途別の射影で内部追加の流入を防ぎ、所有者確認と耐久退避は従来の入力を使う。
診断ログのディレクトリは`agents-server.log`を置く階層とし、`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`が解決する。
Antigravityのイベント処理を調査するときは、上表の公開イベントログを参照する。書き込みに失敗した場合はbackendの警告ログを参照し、イベント処理の終端状態はsession状態から判定する。

索引を読むのは、現行のsession識別子からルートsession識別子を解決する主体だけである。
MCPサーバーは`start`、`send_message`、`list`の応答へ、自身の状態ファイル書込先である`root_session_id`を明示する。`list`はsession一覧が空でも同じ項目を返す。PostToolUseフックは`root_session_id`を持つ応答であれば操作名によらずその項目を索引へ直接書き、子session識別子から状態ディレクトリを逆引きしない。`claude --resume`で起動した会話でもMCPサーバーと会話側の識別子が分かれ、最初のMCP操作が`send_message`になり得るためである。
`atk agents list`と`atk agents wait`は索引または現行識別子自身の状態ディレクトリからルートsessionとの対応を確認する。対応を確認できず対象が0件の場合は、CLIが解決したrootを示し、MCPの`list`を1回呼んで同じCLIを再実行する復旧手順を返す。対応確認済みの空状態は通常の空状態として扱う。
`atk agents notify`は委譲先から`AGENT_TOOLKIT_OWNER_SESSION`で所有者sessionを直接解決するため、索引の読み取りを省く。
子から親へ通知する場合は宛先を所有者sessionから解決する。現行のsession識別子から解決すると、宛先が自分自身になるためである。

資源記録はsession開始・終端時に同じ状態ディレクトリの全rootを読み、重複sessionと失効した状態ファイルを一覧と同じ判定で除く。
発生元が所有するsessionは最新のメモリー状態で上書きする。記録の保存先は既存のagents-server.logとする。
集計は非原子的な観測であり、読取失敗は完全性の欠損として記録する。loadと利用可能メモリーはホスト全体の値であり、session固有の消費量との区別を保つ。

子session・背景taskの完了待ちの保留中へ追送しても、待機対象の識別子と保存出力の対応は保持する。
旧turnの保留結果は公開せず、新しいturnと未完了作業の寿命を分ける。配送失敗時も観測対象を残して再試行できる。
回収した対象だけを追跡から外し、同じ完了による自動再開は一度に限定する。kill・期限・ストリーム終端による明示確定と、利用上限・過負荷の継続は別の境界として扱う。期限を持たない利用上限・過負荷の保留への追送は従来どおり保留結果を確定し、待機対象が残れば`heldResultFinalized`を付ける。

## 判定を確定する前に確認すること

- 上表の「更新できる主体」が2つ以上ある状態は、全ての更新主体の処理の流れを読んで網羅を判定する。
- MCPサーバーのメモリーにだけある状態はMCPサーバーだけが更新でき、`atk`のCLIとフックは更新できない。その状態を判定に用いる処理がCLI経由の操作でも成立するかを確かめる。
- 新しい状態は1つの表現で保持する形へ設計する。未回収の終端結果は、そのファイルを書いた主体の公開台帳と結果ファイルの在否から`published`、`consumed`、`unpublished`へ区分する。CLIが回収途中の結果はrun別退避物を再開時の基準とし、原本が残る中断点では同じ本文を重ねて配送しない。`SessionState.result_delivered`はファイルを削除する契機を表す内部状態であり、ファイル表現を持たない処理に限り用いる。
- MCP待機とCLI待機は`_agents_server/retained_results.py`の`take_result`で所有者の確認、排他取得、本文の読取および削除を1つの区間として実行する。CLI待機は同じ排他区間でrun別退避物を確定してから原本を削除する。MCP待機は退避先を渡さず、従来の回収結果を使う。
- 待機所有権と待機対象の登録は、いずれも書込主体を単位とする名前空間で表す。読む側も同じ単位で読み、探す対象は書込主体を名前とするロックと登録とする。
- `atk agents wait`の前景実行と背景実行は、書込主体ごとに同時に1つとする。ロック競合を観測した後発は`current.json`が示すrun IDを固定し、同じロックの取得後に固定runの状態を読み直す。`published`か`foreground-delivered`なら保存本文と終了コードを返し、返却後に`consumed`へ遷移させて結果の再演を1回に限る。`running`なら保存対象とrun別退避物の対象を引き継ぎ、lock待ち中の追加対象も含めて同じrunの待機を再開する。競合せずロック取得に成功した後続waitも、現在の対象集合が残存する`running` runと一致すれば退避物を引き継いで待機を再開する。原本の回収後にrun別退避物だけへ残るsessionも対象集合の一致確認へ含める。対象集合が一致し、`continuable`がfalseの`published` runは結果を1回返す。対象集合が変わったrunと継続可能な公開runは新規待機へ進む。`consumed`のrunは再利用せず、新規runを開始する。待機上限への到達または通知だけを返したrunは`continuable`をtrueとし、次の発行は新規runを開始する。run記録がない旧形式のlockだけを観測した場合は、旧形式との競合を示す診断で終了する。ロックの単位は書込主体とし、対象sessionごとのロックは除外する。

CLI待機の配送は結果と通知の損失防止を優先する。runへ本文を保存してからstdoutまたはstderrへflushし、その後に配送済みのrun状態を保存する。後続waitが保存済みの本文を配送する場合も、flush後に`consumed`を保存する。どちらの場合でも、flush後かつ配送状態の保存前にプロセスが停止すると、次のwaitは保存本文を1回再出力してよい。強制終了を伴わない同時発行の合流では同じrunを1回だけ回収し、1回の再開処理で同じsessionの結果を重ねて出力しない。flushと状態ファイルの更新を受信確認の仕組みなしに原子化できないため、この中断点での再出力を許容する。

`foreground-delivered`と`consumed`は標準出力への配送と後続waitでの再演だけを表し、受け取ったエージェントが読んだかは表さない。
待機対象が0件の終了（終了コード10）と`consumed`の終了（終了コード8）では、`atk agents wait`が同じルートと書込主体の`current.json`が示す配送済みrunを読み、`run_path`、対象の`session_ids`と実在する`agent_message_paths`を診断へ加える。
受け取った主体は`run_path`を読み、`output`にある終端結果本文へ戻る。本文ファイルが消えていても`output`から読める。
runが無い、読めない、結果を含まない場合は`status`の`missing`・`unreadable`・`no-results`で区別する。復旧先は同じ所有者のrunに限る。
保存runは受け取った主体が読み、`atk agents wait`は結果を再配送せずに1回だけ回収する契約を保つ。

各MCPサーバーは、自身が所有する状態ファイルと対応する一時ファイルに加え、生存の印が失効した他の状態ファイルを削除できる。
生存の印を持たない状態ファイルは保持する。
共有する`results`配下の削除は、委譲元への配送、CLIのrun別退避物への耐久保存、`stop`による明示的な破棄の後に限る。
経過時間は結果削除の契機から外す。
`notices`と`hosts`配下の共有ファイルは、各書込主体が保持期限の経過後に削除できる。
`sessions`配下の登録簿を解放済みへ置換するのは、そのsessionを所有するMCPサーバーだけである。
`stop`または保持期限の経過で資源解放を完了した後に、理由・時刻と内部再開情報を保持するレコードへ置き換える。Codexの資源解放の完了は、所有するApp Serverと子孫の終了を確認した時点とする。
APIへ移行した会話はCodexではprovider、Claudeでは`claude_resume_info`のenvファイルのパスを保持する。
終端を観測した待機側は登録簿を保持する。
解放済みレコードを残すことで、別のMCPサーバーは所有側による解放と記録の不在を区別できる。
委譲元の項目は作成時に書き、状態更新と解放済みへの置換でも引き継ぐ。

lane回収の解放要求は`session_release.py`が登録簿の終端・cwdと所有プロセスを確認して作成する。
所有MCPサーバーの常駐監視が要求を読み、現在の登録と所有を再検収して`stop(retain_result=True)`を実行する。
CLIはmainの待機期限から導出した共通deadline内で応答と資源終了を待ち、使用中判定をやり直す。
自身の要求・応答はCLIの完了時に回収し、異常終了で残ったファイルは既存のsessionファイルの7日掃引へ委ねる。

MCPサーバーの起動時とSessionEndでは、配下の全ファイルの最終更新から7日を超えたrootディレクトリ、`sessions`の登録簿、および`compaction`の計測記録を掃引する。現在の会話のrootは除外し、`compaction`のJSONLとlockは対として削除する。個別の削除失敗を記録し、後続対象の掃引と起動・終了処理を継続する。

backendを開始した後の保存は、開始の成否と別の成否として扱う。
`manager.py`の`_save_started_session`は次の保存の失敗を個別に`persistence_warnings`へ集め、起動応答に`session_id`・cwd・委譲元を残す。

- session登録簿の更新（`SessionState.publish_registration_async`）
- 状態ファイルの射影（`StatusFileWriter.flush_async`）
- 待機対象の登録（`wait_targets.retain_wait_targets`）。`flush`に失敗した起動だけが補う。`flush`が成功した起動は既存の状態ファイルとPostToolUseフックの登録を使う

登録簿の更新は成功した値だけを公開済みとして覚えるため、次の状態更新と`flush`で再試行される。表示用の状態ファイルの置換失敗は`status_file.ProjectionWriteError`として終端結果の保存失敗と区別する。予約した`flush`とheartbeatの保存失敗はログへ残して次回の更新へ進む。
非同期の保存は、パス・本文・登録情報をloop上で確定し、ファイル操作だけを`file_operations.run_file_operation`からworkerへ渡す。成功したsnapshotの反映と保持タイマーの操作もloopが所有し、共有session辞書やwriterをworkerへ渡さない。同期CLIの`atomic_write`と有限再試行の契約は保つ。
loop上の`SessionState.touch`は登録公開をsessionごとのtaskへ集約し、保存中に値が変わった場合も最新の登録まで進める。起動・追送・結果配送の応答は必要な保存を待つ。writerは保存と削除を直列化し、公開済みまたは回収済みの結果を再保存せず、旧turnの保存成功を新turnへ反映しない。
MCPサーバーの停止（`AgentsServerManager.close`）では終端の公開で生じた`ProjectionWriteError`だけをログへ残して後始末を続ける。終端結果の保存の`OSError`は呼び出し元へ伝えたうえで、listenerの解除と`StatusFileWriter.deactivate`を`finally`で行う。
停止が取り消されても、保存taskとworkerの終了を回収し、終端公開と予約解除を終えてから自身の状態ファイルを削除する。保存失敗を成功として扱わず、停止後にworkerが状態ファイルを再作成することを防ぐ。
drainは並行した保存の一部が失敗しても対象workerの全終了を先に回収し、登録公開も終えてから保持した失敗を呼び出し元へ伝える。
共通の`atomic_write`は置換に失敗した例外へ置換元と置換先のパスを注記（`add_note`）するため、呼び出し側が記録するスタックにも両パスが残る。
起動済みとして登録するのはbackendを開始したsessionだけであり、入力検証、候補の解決、backendの開始失敗による不成立は起動前の失敗として返す。

明示列で選択したAPI会話は、API主接続とサブスクからの移行のどちらでも、stopと保持期限の経過による解放後に解放済み記録の内部再開情報から復元する。

- 再起動をまたぐsessionの解決はsession登録簿に従う。statusline向け状態ファイルは書込主体を解決できる処理だけで作成されるため、解決の入力から外す。登録簿が終端を示さないsessionは、別プロセスがturnを実行している可能性を排除できないため再開しない。所有側は停止時に、backendの停止で終わった未終端のturnを`interrupted`として登録簿と結果ファイルへ公開し、記録を終端へ進める。それでも`running`のまま残った記録は、`session_registry.resolve`が記録されたプロセスの識別で判定する。記録された全プロセスが存在しない（pidが無いか、同じpidの起動時刻が異なる）記録は、登録簿を書き換えずに`interrupted`の終端と中断の理由として解決し、全ての読む主体が終端として扱う。プロセスが1つでも存在する記録と、識別を持たない記録（識別の導入前に書かれた記録）は実行中のままとする。照会したMCPサーバーは、生存の印が有効な状態ファイルがどれもそのsessionを載せていない場合に限り、この中断をengineを問わず登録簿へ公開して復元へ進む。Codexの記録は、同じ前提で自身のCodex App Serverへの`thread/read`が登録簿の`turn_id`のturn（項目の無い記録では全turn）の終端を示し、後続に進行中のturnが無い場合も、その終端状態を公開する。引き継ぐ側が行うのは終端の公開だけとし、解放済みへの置き換えは所有側に残す。
- 登録簿のレコードが不在、解放済みまたは読取不能である場合は、同じ識別子の終端結果ファイルを確認し、そのファイルが終端を示す場合は終端として扱う。解放済みと不在は所有側の解放と7日超の掃引のいずれからも生じ、終端結果の在否と独立するため、終端結果の有無の判定には結果ファイルを用いる。解放済みの公開判定と待機対象の解放・実行中の判定は不在と同じに扱う。明示列で選択したAPI会話の復元だけは、解放済み記録の内部再開情報を消費し、同じproviderをbackendへ渡す。古い記録と他engineの復元条件は従来のままとする。

## 本書の更新が必要になる変更

- 上表のいずれかの状態について、基準となる保持先、読む主体または更新できる主体を変える変更。
- 状態ディレクトリ配下へ新しい種類のファイルを置く変更。
- MCPサーバー、`atk`のCLI、フックおよびstatuslineのいずれかへ、既存の共有状態を読み書きする処理を追加する変更。
