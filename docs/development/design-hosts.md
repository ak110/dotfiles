# ホスト間の規範配置と適用境界の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## Claude CodeとCodexの規範配置

### 共有原本とCodex固有の上書き

Codexのコマンド回収は、外側の`functions.exec`のcellと内側の`exec_command`のsessionを別の実行識別子として扱う。
返却本文とともに`exit_code`と`session_id`を保持し、cellは`functions.wait`、sessionは`write_stdin`で同じ起動の終了まで回収する。
`session_id`がある間に同じ待機コマンドを再発行すると、先行実行を回収する前に次の待機も開始するため採用しない。
契約の定義元は`rules-common.codex.md`へ集約し、Codex向けAGENTSの正式生成で全主体へ届ける。

hook・MCP定義などホスト別に明確に分離された資源を除き、Claude CodeとCodexに共通するルール・スキルは`agent-toolkit/`の共有原本で定義する。
Codexだけの公開能力との差分は上書きとして置き、常時規範へCodex固有の条件を持ち込まない。Codexの全主体へ適用する差分は`agent-toolkit/share/rules/rules-common.codex.md`へ置き、`scripts/sync_codex_agents.py`が`~/.codex/AGENTS.md`へ埋め込む。Codexのメインだけへ適用する差分は`agent-toolkit/share/rules/rules-main.codex.md`へ置き、`SessionStart` hookがCodexのメインへ`rules-main.md`の後に加える。`~/.codex/AGENTS.md`はCodexのサブエージェントと委譲先にも届くため、メイン向けの差分を同じファイルへ置くとメイン専用の条文が全主体へ届く。
Codex基礎指示の上書きは、確認・待機・並列化・ツール利用前説明のホスト契約をCodex側へ閉じ込め、Claude Codeの共通契約を変更しない。
Codexの委譲は、`spawn_agent`・`send_message`・`followup_task`・`wait_agent`・`interrupt_agent`を使うネイティブ方式とagents_server方式を分ける。ネイティブ方式では`fork_turns`が選ぶ会話履歴、worktreeの`AGENTS.md`、`SubagentStart` hookが配送する`rules-subagent.md`を独立した入力とする。agents_serverの通常委譲は`_agents_server/launch_prompts.py`から同じ共通委譲先規範を配送する。各起動区分の委譲先へ届く規範は`agent-toolkit:delegation/references/delivery-scope.md`の配送範囲表が示す。Claude Code固有の委譲先規範はCodexへ配送しない。この境界をhookの実起動、agents_serverのprompt構成、Codex manifestの生成テストで確認し、規範本文を方式ごとに複製しない。
`scripts/sync_codex_agents.py`はCodex基礎指示と共有ルールから生成物を作成し、`scripts/sync_generated_files.py`が正式な一括生成の窓口となる。
`scripts/sync_codex_agents_test.py`は共有原本と生成物の同期、共有契約の保持およびCodex固有上書きの配置を確かめる。
生成物は手編集せず、原本と正式生成器を更新して同期する。

共有ルールをCodex固有条件で分岐する案は、Claude Codeへ不要な差分を配布して共通契約を曖昧にするため採用しない。
共有文書をCodex用に複製する案は、原本・生成器・自動テストの同期対象を増やすため採用しない。
Codex事情を共有ルールへ直接改訂する案は、Claude Codeへホスト固有の挙動を波及させるため採用しない。

### plugin manifestと生成物

公開動作を追加または変更するpluginは、各pluginの`.claude-plugin/plugin.json`（`agent-toolkit/`と`agent-basics/`のそれぞれ）を版数の基準とする。
版数は`agent_toolkit_bump.py --plugin <plugin名> minor`などの正式な手順で更新する。
`.claude-plugin/marketplace.json`の各plugin記述は、同名pluginの基準の版数と一致させる。
対象にできるpluginは`marketplace.json`の`plugins[]`から導き、pluginを追加しても版数更新の手段を別に用意しない。
`agent-toolkit/plugin.json`と`agent-toolkit/.codex-plugin/plugin.json`は`sync_generated_files.py`で生成する。
`agent-toolkit-codex/`はGitで追跡せず、post-applyがCodex plugin導入の直前に`pytools/_internal/codex_plugin_manifests.py`で生成する。
同ディレクトリは`agent-toolkit/`を原本とする自己完結型の通常ディレクトリであり、Agent Plugins用の直下`plugin.json`と`mcp.json`を除外する。
`.agents/plugins/marketplace.json`のCodex local sourceだけを`./agent-toolkit-codex`へ向け、Claude CodeとAgent Pluginsのsourceは`agent-toolkit/`のまま維持する。
手動生成には`scripts/sync_codex_plugin_manifests.py`を実行する。
生成後は同scriptの`--check`でClaude Code向け原本、marketplace記述、Codex向け派生manifest、`agent-toolkit-codex/`全体の一致を確認する。
プラグインマニフェストの検証は、Claude Code向けとCodex向けで到達できる保証の水準が異なるため手順を分ける。
Claude Code向けは`claude plugin validate --strict`を`pyfltr`のカスタムlinter`claude-plugin-validate`から実行し、未知フィールドとメタデータ欠落を失敗として扱う。
Codex向けはCodex同梱の`plugin-creator/scripts/validate_plugin.py`を`pytools/_internal/codex_plugin_manifests_test.py`から実行し、指摘の集合が既知の2件と完全に一致することを確かめる。
Codexの検証器を無条件の合格条件にしないのは、同梱資料が`hooks`をマニフェストの正規フィールドとして定義しながら、検証の節では未対応フィールドとして拒否すると述べ、同一資料内で矛盾しているためである。
資料はローカル導入での受理を保証も否定もせず、現行manifestのままの導入状態は`installed: true`かつ`enabled: true`である。
そこで既知の逸脱の集合を固定し、集合が変化した時点で失敗させて再判断の契機とする。
知識境界として、期待する逸脱の集合と許容根拠はテスト側が持ち、マニフェストの生成規則は`pytools/_internal/codex_plugin_manifests.py`が持つ。
却下した代替案は、`hooks`を除去し`mcpServers`を`.mcp.json`へ解決させて検証器を無条件に合格させる案である。Codexのプラグインフック機能を失い、`agent-toolkit/.mcp.json`との名前衝突を解消する追加設計を要する一方、得られるのは資料上の保証が無い体裁上の適合だけであるため採用しない。

### Codexの起動管理とdaemon復旧

通常TUIの起動管理は`pytools/codex_launcher.py`が所有し、`bin/codex`とWindowsのペアから導入済みの`dotfiles-codex`を起動する。
Codexの起動中は依存関係を導入せず、post-applyと同じpytools環境を使う。
公式standaloneの実行体は改変せず、Linuxの可視リンクとWindowsのPATH順を導入処理で調整する。
Windows公式インストーラーの可視binはpackageのbinへのjunctionであるため、その中へwrapperを書き込まず既存dotfiles/binを優先する。
実行時の`CODEX_HOME`は設定・認証・会話の保存先として保持し、導入した実行体の選択から分離する。
Linuxは可視コマンドの接続先をwrapperへ変更する前に、公式のcurrentへの参照を同じbinの`.dotfiles-codex-native`へ1個保持する。
更新時はこの参照を原子的に交換し、Windowsは可視binに残した公式実行体を直接使用する。
元の標準入出力とTTYはnativeへ継承し、公開`--remote`のloopback WebSocketと`app-server proxy`の間だけを中継する。
待受は起動ごとの認証トークンを必須とし、RPC本文は中継の寿命内のメモリーにだけ保持する。
別の常駐サービスとTTY代理は設けない。

`plugin`・`mcp`・`exec`・`--version`・`completion`などの非対話コマンドは、hookからも繰り返し起動される。
起動管理はこれらを、復旧用の依存（psutil・pydantic・asyncio・中継モジュール）を読み込む前に同じ公式実行体へ引き渡す。
実行体の選択は標準ライブラリだけの`pytools/_internal/codex_runtime.py`が持ち、導入処理と起動管理が共用する。
非対話コマンドが復旧用の依存を読み込まないことは`pytools/codex_launcher_test.py`で試験する。

会話は実際の`thread/start`・`thread/resume`応答の正確なIDで選び、一時的なタイトル生成threadや同じcwdの別会話と区別する。
接続断またはdaemon世代の交代を観測すると、公開`initialize`・`thread/resume`・`thread/turns/list`で保存済み状態を確認する。
元turnが未完了で現在のwriterが無く、明示中止と新しいユーザーturnも無い場合だけ`turn/start`で未完了工程の継続を求める。
通常の会話開始・resumeとdaemon復旧の双方で、会話に属するagents_serverの`list`を公開MCP RPCから呼び、モデルの最初のツール要求へ依存せず共通の子孫復旧へ接続する。
保存済みIDの再開では、子MCPが起動時に直接親を確定できるよう、`thread/resume`の送信前にhost索引を公開する。
応答後に公開すると、子MCPが所有rootを直接親として孫の復旧候補を確定してしまう。
要求の応答を失っただけで副作用のある操作を再送せず、会話へ保存済みの状態を照合してから続ける。
応答を失ったRPC名と、元の依頼・実行履歴・操作対象の状態を同じ会話で確認する入力方法をTUIへ返す。

nativeの世代記録は`processIdentity`を省略可能な項目とし、Linux・macOS以外（Windows）では書かない。
世代は`pid`と`processIdentity`で識別し、`processIdentity`が無い記録は全OSで書かれる`processStartTime`で識別する。
daemon 0.166.0は世代ファイルを空の予約ファイルとして直接作成し、その後別inodeの完全なJSONへ交換する。
ファイルの存在を完成済みJSONの保証にせず、空本文だけを有限の未準備状態として扱う。
非空の不正JSONと期限を越えた予約状態は復旧失敗にする。
旧daemonの承認要求IDを中継側で分離し、再起動後に同じ数値IDが再利用されても旧応答で承認しない。
復旧用RPCがMCPの対話要求を待つ間も、TUIからの応答は現在の接続へ中継する。
旧要求は接続を閉じる前に無効化し、ユーザーの明示終了では対話待ちの復旧を中断して所有資源を回収する。
通常終了・明示中止・完了済みturnを復活させず、所有TUIがdaemon交代で終了した場合だけ同じIDと保存した条件で再起動する。
非対話の`codex exec`とagents_serverの専用App Serverは共有daemonに依存せず、その経路を置き換えない。

### Codexからの更新jobと結果の保持

`CODEX_THREAD_ID`を持つ起動では、`bin/update-dotfiles`とWindows版ランチャーが、uvの外側のnative Pythonで`libexec/update_dotfiles_job.py`へ処理を渡す。
通常端末の更新は既存のTTYとSSH入力を保持する。
独立workerがuv自己更新、更新本体、成功後の必要なpruneを順に所有し、起動元のdaemon再起動でcontrollerが終了しても処理と終了結果の保存を継続する。
POSIXは新しいsession、Windowsは独立したprocess groupとjobからの分離を使い、分離失敗を成功扱いにしない。

jobはUUIDで識別し、既存の状態領域の`update-jobs/<UUID>/`へ入力引数・開始終了・終了コードと分離した標準出力・標準エラーを保存する。
環境変数全量は保存しない。
公開`update-dotfiles job`は同じ`CODEX_THREAD_ID`の最新job、`update-dotfiles job <UUID>`は明示jobの保存結果を取得する。
再取得では更新を再実行せず、未完了を完了と区別する。
workerは有限の実行上限と子孫の回収を持ち、通常のCtrl+Cは所有workerへの停止要求と終了回収へ接続する。
完了済みjobは7日で回収し、未完了jobと出力は保持する。

uv内で更新jobを待つ構成は、起動元の停止に更新処理が巻き込まれるため採用しない。
daemon再起動後の会話復旧は既存の起動管理へ接続し、更新jobに別の常駐serverや会話復旧機構を追加しない。

### Codexのlocal plugin導入

dotfilesの`post_apply`によるローカルagent-toolkit導入は、Codex CLIを準備した後に`agent-toolkit-codex/`を生成し、マーケットプレイス登録とplugin導入をCodex公式CLIへ委譲する。
`agent-toolkit-codex/`の生成に失敗した場合は、post-applyのstep失敗として永続logへ記録し、全体を非0で終了する。
`install_codex_plugins.py`は原本manifestの版数と`codex plugin list --json`の導入状態を比較し、未導入か、有効なpluginの版数不一致の場合に`codex plugin add <plugin-id>`を実行する。
明示的に無効なpluginは導入状態とcacheを保持し、版更新も保留する。明示的な再有効化後に通常の更新を再開する（[Codexガイド](../guide/codex-guide.md#プラグイン更新の反映)）。
CLI成功後は同コマンドで`codex plugin list --json`を取得し、版数一致と有効状態を検証する。
実際のaddまたはupdateとhook状態の確認を完了した後、daemonが稼働中であれば再起動方針を1回だけ適用する。
利用セッションがあっても`codex app-server daemon restart`を自動実行し、同じ会話と子孫の継続は[Codexの起動管理とdaemon復旧](#codexの起動管理とdaemon復旧)へ委ねる。
Linuxの`update-dotfiles`は適用差分があり利用セッションが無い場合だけ管理daemonをapply前に一時停止し、apply後にもセッション不在を確認して起動状態を戻す。
自動再起動の成功と失敗は終了コードとともにupdate-dotfilesログへ記録する。
再起動失敗はplugin導入を巻き戻さず、`PostApplyOutcome`から更新全体の非0終了と次の操作へ伝播する。
daemon再起動の終了コード0と、会話・子孫の復旧成功は別の観測として扱う。
restartの外側上限は、nativeの停止猶予を途中で打ち切らないようにする。
Codex 0.166.0の操作lock待機375秒、停止猶予最大300秒、強制終了確認10秒、起動30秒と応答の余裕を含め、restart専用に750秒を確保する。
根拠は[対象版のdaemon実装](https://github.com/openai/codex/tree/rust-v0.166.0/codex-rs/app-server-daemon)であり、通常のplugin照会は従来の上限を使う。
無変更、marketplace登録だけの変更、daemon停止中および不要pluginの除去は自動再起動の対象に含めない。
`atk config`へ設定を追加する案は、dotfiles更新処理だけが消費する真偽値のためにagent-toolkit pluginとpytoolsの設定契約を結合するので採用しない。
公式資料の[Plugins](https://developers.openai.com/plugins/build/plugins)はローカルpluginを`~/.codex/plugins/cache/<marketplace>/<plugin>/<version>/`へ導入し、導入先の実体をCodexが読み込むと定める。
現行versionの導入はCodex CLIへ委ねる。
更新処理は`plugin add`の前に通常のversionディレクトリを複製し、導入後の状態確認とdaemon処理を含む最後のCodex CLI呼出後に、CLIが削除した旧versionだけを同じ絶対パスへ戻す。
退避から復旧までを1つの保護区間とし、復旧後はCodexを使う処理へ戻らず更新関数を終了する。これにより個別コマンドのcache副作用を前提とせず、更新関数の終了時に既存セッションが参照する全旧パスの存在を保証する。
Codex 0.163.0では、App Serverの`plugin/list` RPCもlocal marketplaceの新versionを自動導入し、更新関数の外で全旧cacheを削除する。
この上流のcache寿命不整合は[openai/codex#31383](https://github.com/openai/codex/issues/31383)で複数版・複数OSから報告され、live hookの再読込も[openai/codex#36605](https://github.com/openai/codex/issues/36605)などで未解決である。
dotfiles向けの生成済みhook定義は、版付きcacheの外へ先に配置する`atk-hook`を使う。
`PLUGIN_ROOT`が指す版付きCodex cacheが欠落した場合、同版のClaude Code plugin cacheを検証して元のCodex pathへ原子的に復旧する。
同版が無い場合はClaude Codeの現行導入版からhookだけを起動し、編集途中の作業ツリーは使わない。
共有配布物のhook定義は、dotfilesとClaude Code cacheを前提にできないため`${PLUGIN_ROOT}`から起動する可搬契約を維持する。
`plugin add`が失敗した場合は退避した全versionを更新前の状態へ戻す。
目標versionはCLIが作成した内容を維持し、復元対象から外す。
CLI導入後の検証が終わるまでlegacy skillリンクを除去せず、リンク除去に失敗した場合だけ変更前snapshotから復元する。
`plugin add`後に失敗してもCodexの管理状態を自前で書き戻さない。
Codex側では外部pluginを導入せず、不要な`compact-plus@compact-plus`が導入済みの場合だけ`codex plugin remove`で除去する。
除去成功時にdaemonが稼働していれば再起動案内を追加し、一覧取得または除去に失敗した場合はローカルplugin処理を継続する。
Codex側の外部marketplace登録、外部plugin導入、取得元検証は対応先がないため撤去する。
Claude Code側も不要な外部`compact-plus@compact-plus`を除去する。圧縮後のユーザー発話の復元はagent-toolkit自身のhookが担う。
post-apply案内とWindowsのjunction除去処理は維持する。
cache version台帳、POSIXの原本接続、Windowsの原本接続とファイル同期は設けない。
旧versionの実体は、保存済みセッションが保持した絶対パスを再開後も使えるように更新処理が保持する。

シンボリックリンク方式は代替案として却下する。
Codex 0.154.0はroot直下のAgent Plugins用`plugin.json`を`.codex-plugin/plugin.json`より優先し、同一rootから導入した場合は`hooks/list`が0件となる。
また、相対シンボリックリンクを含むCodex専用wrapperを公式CLIで導入すると、snapshotには`.codex-plugin`だけが残り、リンク先のhook・skill・実行資源が含まれない。
そのため、`agent-toolkit-codex/`は全資源を通常ファイルとして生成し、公式CLIへsnapshotと版数別cacheの管理を委ねる。
検証条件と再検証手順は「[docs/development/design-hosts.md：Claude CodeとCodexの規範配置：2026年9月13日](audit-records.md#docsdevelopmentdesign-hostsmdclaude-codeとcodexの規範配置2026年9月13日)」を参照する。

### plugin実体の回収と稼働中の参照

更新・回収・修復でplugin実体を削除または置換する処理は、消費側が保持する絶対パスを有効なまま保つ。
稼働中のhook・skill・MCPは起動時に解決したplugin rootの絶対パスを使い続けるため、参照中に旧版を削除すると実行中の処理が`[Errno 2]`などで失敗する。
保存済みセッションはプロセスとして存在しない期間も後から再開できるため、プロセス一覧は参照の終了を表さない。
agent-toolkitの更新では旧versionを自動復旧し、Codexの稼働状態を更新可否の判定に使わない。
実体そのものを除去する処理は、参照の終了を判定できない間、既存の実体を保持して延期する。

| 処理 | 参照の判定 | 延期後の再開 |
| --- | --- | --- |
| Codexのpost-apply更新（`install_codex_plugins.py`） | 更新前の全通常versionディレクトリを複製し、CLIが削除した旧versionを同じ絶対パスへ復旧する | 同じ実行で更新と旧versionの復旧を完了する。復旧失敗は導入成功と区別して報告する |
| 単体インストーラー（`install-claude.sh`・`install-claude.ps1`） | post-apply更新と同じく、更新前の全通常versionディレクトリを複製する | 同じ実行で更新と旧versionの復旧を完了する。復旧失敗時は退避先と報告先を表示して非0で終わる |
| Claude Codeの旧版cache整理（`prune_claude_plugin_cache.py`） | `claude_plugin_references.py`。その版の配下を指すプロセスと、その版が現行だった期間に起動したClaude Codeセッション | 参照の終了後の`update-dotfiles`で削除する |
| Claude Codeの現行版cache欠落修復（`install_claude_plugins.py`） | `claude_plugin_references.py`。導入先を指すプロセスと、起動時刻を問わない稼働中のClaude Codeセッション | post-applyの`deferred`と延期記録による自動更新タイマーの再試行。タイマーを持たないホストではClaude Codeのセッションを全て終了してから`update-dotfiles`を再実行する |
| Codexの不要pluginの除去（`install_codex_plugins.py`の`_remove_unused_plugins`） | `codex_processes.py`の役割分類。`session`だけを数え、管理daemonとその補助実行体・更新ループは数えない | post-applyの`deferred`と延期記録による自動更新タイマーの再試行。タイマーを持たないホストではCodexの終了後に`update-dotfiles`を再実行する |

Claude Code 2.1.303の`claude plugin uninstall`とmarketplaceの削除は、plugin cacheの実体を孤立の標識（`.orphaned_at`）付きで残す。他のscopeが残らないpluginでは永続データ領域（`plugins/data/<id>/`）を削除する（いずれも隔離した設定ディレクトリでの観測）。`install_claude_plugins.py`のdeprecated pluginの除去は、実体を削除せず、データ領域も`--keep-data`で残すため、稼働中のセッションが使う実体を削除する処理に当たらず、延期を設けない。`claude_marketplace.py`の登録修復は、marketplaceの削除で収録pluginを除去してデータ領域を削除し、同じ実行で再導入する。収録するagent-toolkitの`plugin_environment.py`の`runtime_data`はユーザーデータ領域（Linuxでは`XDG_DATA_HOME`配下の`agent-toolkit`）を先に選び、そこへ書き込めない場合だけデータ領域（`CLAUDE_PLUGIN_DATA`）を使う。`plugin.json`は設定項目を持たない。このため通常の環境では稼働中のセッションのhook・MCPはデータ領域を参照せず、削除しても動作は変わらないので、この修復は参照の終了を待つ延期の対象にしない。
daemon自動再起動と旧versionの保持は独立している。
旧cacheを移動して退避する案は、移動中に元のパスから実体へ到達できないため採らない。
更新前の実体を残したまま複製し、CLIの完了後に削除された旧versionを復旧する。
最新版へhookを付け替える案は、旧版のskill・MCP・遅延importを守れないため採らない。
プロセス終了まで更新を延期する案は、後から再開できる保存済みセッションを判定できず、Codexを常用する環境で更新が完了しないため採らない。

### ルールの配置と配送

`agent-toolkit/share/rules/common/`配下にはメインエージェント、サブエージェントおよび委譲先の全てへ適用する条文だけを置く。
メインエージェントだけに適用する条文は`agent-toolkit/share/rules/rules-main.md`へ置き、Claude Code固有分は`agent-toolkit/share/rules/rules-main.claude-code.md`へ置く。
サブエージェントと委譲先だけに適用する条文は`agent-toolkit/share/rules/rules-subagent.md`へ置く。
メイン向け条文は`SessionStart`フック（`agent-toolkit/agent_toolkit/_hooks/rules_context.py`）が全ての`source`で文脈へ追加し、会話圧縮の後も再度追加する。
サブエージェント向け条文は`SubagentStart`フックが全てのagent種別へ追加する。`agents_server`の通常起動の委譲先には、`agent-toolkit/agent_toolkit/_agents_server/launch_prompts.py`の`DELEGATE_SYSTEM_PROMPT`が同じ条文を連結し、両backendのシステム指示として渡す。
`SessionStart`フックは`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`の場合と`AGENT_TOOLKIT_OWNER_SESSION`が設定されている場合を`agents_server`の子と判定し、メイン向け条文を追加しない。
後者を併用するのは、Codex backendがstatusline表示の判定のために子のApp Serverから前者を除くためである。
知識境界として、条文の本文は`share/rules/`配下の各ファイルが持ち、フックと`agents_server`は本文を読んで渡すだけで内容を判定しない。
Codex向けAGENTS.mdの生成器は`share/rules/rules-common.codex.md`と`share/rules/common/`配下の共通条文を埋め込み、役割別の`rules-main*.md`・`rules-subagent*.md`を埋め込まない。
Claude Codeの`additionalContext`文字列は10,000文字の上限へ合わせ、メイン向け条文の合計を同上限内に保つテストを`agent-toolkit/agent_toolkit/_hooks/rules_context_test.py`へ置く。
ファイル保存と2,000文字のプレビューは別の出力契約として扱い、それぞれの観測条件と保証範囲を監査記録へ残す。
却下した代替案は3つある。
Codexメイン向け条文をAGENTS.mdへ生成器で埋め込む案は、Codexのサブエージェントにもメイン向け条文が届き、両ホストで配布手段が非対称になるため採らない。
委譲先向け条文もフックだけで届ける案は、Codex backendの委譲先でフックの信頼登録と環境変数の印を必要とし、未設定の環境で条文が欠落するため採らない。
Codex backendの子のApp Serverへ`AGENT_TOOLKIT_DELEGATED_SESSION`を渡す案はClaude委譲先の中で起動したCodex委譲先がstatusline表示でClaude委譲先と誤判定されるため採らない。

Claude Codeが自動で読み込む規範は、Codexでは次のいずれかの手段で届けるか、対象外とする理由を記録する。
Codexへの配送手段を追加・変更する主体は、この対応で届かない規範が無いかを確かめる。
読込指示は`agent-toolkit/share/rules/rules-common.codex.md`「Claude Code向けに配置された規範の読込」が定める。

| Claude Codeの自動読込対象 | Codexでの届け方 |
| --- | --- |
| `~/.claude/rules/agent-toolkit/`配下（`agent-toolkit/share/rules/common/`の共通条文） | `~/.codex/AGENTS.md`への埋め込み（`scripts/sync_codex_agents.py`） |
| `share/`の主体別条文（`rules-main.md`・`rules-subagent.md`） | `SessionStart`・`SubagentStart` hookと、`agents_server`の委譲先では`developerInstructions` |
| `~/.claude/rules/myprojects-common.md` | `~/.codex/AGENTS.md`への埋め込み（同スクリプトの`PERSONAL_SOURCE`） |
| `~/.claude/rules/myprojects.md` | Codexのメインと組み込み委譲先には届けない。ホスト別のプロジェクト一覧と同期方針であり、`myprojects-common.md`冒頭がClaude Codeへの配布と定めるためである |
| `~/.claude/rules/`直下の`*.local.md`と`~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md`からの読込指示 |
| プロジェクト直下の`.claude/rules/`、`AGENTS.md`が無い場合の`CLAUDE.md`、`CLAUDE.local.md` | `~/.codex/AGENTS.md`からの読込指示 |

ユーザー単位の`*.local.md`と`~/.claude/CLAUDE.md`の2種類は実行ホストごとにユーザーが置くファイルであり、リポジトリから生成する`~/.codex/AGENTS.md`へ埋め込めない。読込指示は実行時のファイルの有無によらず共通であるため、プロジェクト直下の`.claude/rules/`と同じ手段を選ぶ。
2026年10月6日に、`agents_server`で起動したCodexの委譲先が、`~/.claude/rules/`直下の`*.local.md`が使えないと定めたコマンドを実行して失敗した。当時の読込指示はプロジェクト直下の3種だけを挙げ、ユーザー単位の規範を含めていなかった。
却下した代替案は次の2つである。
`SessionStart`・`SubagentStart` hookで`*.local.md`の本文を注入する案は、出力量がホストごとに変わり、hookの出力上限を外せるhandlerの条件（出力量が条文ファイルで固定され、上限を確かめるテストで拘束される）を満たさない。作業に該当しないファイルも毎回の起動で文脈へ入り、hookの信頼登録を要する条件もCodexのメインに残る。
`agents_server`の`developerInstructions`へ本文を連結する手段だけで届ける案は、Codexのメインとネイティブのサブエージェントに届かず不足する。この連結は`agents_server`の委譲先へ届ける手段として併用する（`design-agents-server.md`「委譲先への指示と起動時の規範」）。起動時の指示に同じファイルの本文が境界付きで含まれる場合は、読込指示の側で読み直さない。

`agents_server`の`start`で起動したClaude backendの委譲先は、`~/.claude/rules/agent-toolkit/`配下の3ファイルの全文をユーザー規範として保持し、起動時のシステム指示と区別する。
また、`agent-toolkit/share/rules/rules-subagent.md`を起動時のシステム指示として保持する。
`agent-toolkit/share/rules/rules-main.md`は保持しない。
2026年9月7日に`agents_server`の`start`で委譲先を1件起動し、受領したエージェント向け文書の一覧と個別条文の有無を返させて確認した。
再検証は同じ起動を1件行い、`02-agent-operations.md`の見出しと`02-agent-operations.md`の任意の条文の有無を返させる。
委譲先へ届く条文の範囲は実際に動かして確定し、届いている条文を`agent-toolkit/share/rules/rules-subagent.md`へ重複して置かない。

## 認証情報の扱いとauto mode検証

ユーザーの認証情報ファイルは、設定ディレクトリを変えない場合にホストが読む位置に残し、委譲の作業領域へ移さない。
配布設定はClaude Codeの組み込み`Read`を拒否し、常時規範は委譲元と委譲先による再配置を禁止する。
委譲手順は認証を要する検証で設定ディレクトリを変えず、ホストが通常読む認証情報を使う。
組み込み`Read`の拒否はBash経由の複製を遮断しないため、本設計は配布設定・常時規範・委譲手順の3層へ防止の役割を分担させる。

認証を要するauto mode検証では、`auto-mode config`が現在の設定反映を確認する。
配布元の対象固有テストは対象の配布元が所有する契約をチェックし、比較可能な`critique`だけが新規・悪化指摘を検出する。
各チェックは担当する契約だけを知り、対象固有テストをauto mode classifierの判定のテストとして扱わない。
比較不能な総合出力または実在しないauto mode classifierのテストを必須にする案は、変更の成否を識別できないため採用しない。

## ユーザーとの認識合わせ

`agent-toolkit:user-confirmation-and-report`「認識合わせ」は、メインが理解している目標と問題を自分の言葉で提示し、
その理解をその場で確かめる起点を担う。質問の構成、回答の取得、無回答時の退避は同スキルの「確認要否の判定」「手段の選択」が扱い、回答後の見直しは既存のユーザー指摘の受領へ接続する。
協調・自律の両モードを対象とし、ホストの質問機能と期限の差を共通の確認手順で扱う。

2026年10月6日まで、この手順は独立したスキル`agent-toolkit:realign-with-user`が持ち、自律モードの扱いを同スキルと`agent-toolkit:user-confirmation-and-report`が互いに参照し合っていた。確認規範を1つのスキルへ集約するユーザーの指示と確認回答（同日）により、手順を`agent-toolkit:user-confirmation-and-report`の節と「手段の選択」の表の認識合わせの行へ移し、スキルを廃止した。`/agent-toolkit:realign-with-user`による手動起動が無くなることは、同じ確認回答で了承済みである。
確認の要否は同スキル「確認要否の判定」が、目的・期待結果・認可の確定と、その範囲内の手段の選択を分けて決める。2026年10月3日まで、ユーザーだけが持つ値の違いで推奨案が変わるかの1判定とし、値ごとの推奨案を書けない問いを確認不要へ閉じていた。この扱いは未特定の不確実性と、確認済みで推奨が変わらない状態を区別できず、UWI書式とホスト別規範にも同じ扱いが再掲されていた。共通の判定を一般化し、直接消費側を参照へそろえた。技術判断をユーザーへ委ねる反復を防ぐ従来の目的は、目的と認可が確定した範囲の手段を自ら決める扱いで保つ。確認を一律に増やす案と、手順の追記だけで済ませる案は採らない。

## 規範の集約先と参照方向

WI処理の運用形態、登録と回答、振り返りからの投入の流れをエージェントが横断して参照するため、`agent-toolkit:workflow-overview`をオンデマンドの知識スキルとして置く。人間向けの`docs/guide/claude-code-guide.md`はエンドユーザーの導入と操作の案内を保つ。プラグインからdotfiles固有のガイドを参照できない配布範囲と、全工程が読む`share/workflow-phases.md`の読込費用を分けるためである。ガイドだけを読む契機の追加、ガイドの内容をプラグインへ移して縮める案、工程責務の契約へ運用概要を追記する案は、この境界または読込費用を守れないため採用しない。

複数の実行主体が使う契約は、契約本文を1箇所の基準文書へ集約し、各主体の文書には読む時点と利用場面固有の差分だけを残す。一括取得は`agent-toolkit:add-awi-by-user`、採否レコードは`agent-toolkit:process-wi`、履歴書換えは`agent-toolkit:commit`、レビュー観点は`agent-basics:review-standards`が定める。スキル本体には起動判断、工程順序および停止条件を残し、実施時だけ必要な表記規則や欄仕様は`references/`へ分離する。

判断から一意に導出できる実装順序・分解粒度・コマンド列は、安全性、データ保全または公開契約を保護する場合を除き、各エージェント文書へ常設しない。

委譲元は割当と認可を知り、委譲先は自身の実行手順と出力を知る。この知識境界を維持するため、同じ契約を`<役割名>.parent.md`とagent定義へ複製しない。定義元の指定と本文複製を併存させる案は同期の抜けを残すため採用しない。共通文書を契約ごとに新設する案も参照段数を増やすため、既存の責務所有者へ集約できる場合は採用しない。

委譲の名前付き入力は、`agents_server`の`start`、委譲先での取得結果、`<役割名>.subagent.md`が定める権限と入力および他の受領値から導出できない状態だけを持つ。省略時の値と行が無い場合の解釈は`<役割名>.subagent.md`が定め、`<役割名>.parent.md`はその省略時の値と異なる場合だけ送信する。この分離は値の二重所有を避けるための契約であり、`start`の`cwd`や委譲先がGitから得る値を送信一覧へ再掲しない。

委譲の継続可否条件は`agent-toolkit/skills/delegation/references/continuation.md`「継続と新規起動」だけで定め、他の文書へ再掲しない。
`agent-toolkit/share/exec.parent.md`は工程順と渡す入力だけを定め、判定条件は同節を参照する。
同じ判定条件を複数の文書へ複製した結果、暫定修正で1文書だけが旧規定のまま残り、実装担当が矛盾を報告する事象が発生したためである。

サブエージェントを起動する手順は、`<役割名>.parent.md`と`<役割名>.subagent.md`のペアへ分け、委譲先の所在にかかわらずプラグインの`share/`配下へ置く。複数のスキルから同じ役割を起動する場合も、いずれの委譲元も`${CLAUDE_PLUGIN_ROOT}/share/<ファイル名>`という同じ形式で参照先を解決できる。`<役割名>.parent.md`は起動手順、渡す入力、受領する報告と検収条件だけを持ち、委譲先固有の作業手順を複製しない。`<役割名>.subagent.md`は責務、入力、実行手順および出力形式だけを持ち、委譲元の割当・認可判断を複製しない。
却下した代替案は、`<役割名>.subagent.md`を各スキルの`references/`配下へ置く案である。複数のスキルから参照される役割では文書の所在が一意に定まらず、同じ役割の起動手順が2箇所へ分かれるため採らない。

スキル本体が持つ規範を`references/`配下へ移す統合では、`references/`配下の資料が互いに規範を委ねる状態が構造的に生じる。このため、資料間の委譲は`SKILL.md`が同じ読込条件で併読を定める兄弟資料に限って認め、併読を定めない兄弟資料へは規範を委ねない。委譲の可否を決める責務は`SKILL.md`の読込条件が持ち、委ねる側と委ねられる側を同じ工程へ並べる。
移設対象の全参照を各資料へ自己完結させる案は採用しない。同じ規範本文を複数の資料へ複製することになり、定義の単一性を失うためである。

失敗・拒否・上限超過・遮断のときだけ実行する回復手順は異常が起きたときに読み手へ届く手段へ置く。異常の有無にかかわらず全文を読む文書には起動契機だけを残す（`agent-basics:writing-standards/references/agent-documents-additions.md`「失敗時の回復手順」）。この配置の根拠は常時の文脈に置いた失敗の教訓が平常時の成績を下げるという報告である。論文arXiv 2610.02994は、失敗の教訓を常時の文脈から除くとWebShopとMind2Web Replayの成績が上がったと報告する。評価したモデルはQwen3.5-9BとGPT-OSS-120Bに限られるため、規範側は努力目標として扱う。

## 場面別手順と外部投稿前レビュー

managed-temp、品質確認、検索の手順は、それぞれ`agent-toolkit:managed-temp`、`agent-basics:check-execution`、`agent-basics:search`が所有する。作業ツリー外の秘匿値ファイルは`agent-basics:secret-files`が扱う。作業ツリー内のファイルは通常の手段で扱い、外部の認証情報を参照する場合は実体の位置で内外を判定する。実行時に必要な場面を各スキルの`description`で示す。手順本文を場面別スキルにまとめ、常時規範から長文を読む工程を減らす。委譲の親子契約は前節の境界に従って引き続き`share/`へ置く。スキル起動は`description`から判定するため、常時規範からの決定的な読込契機は持たない。

第三者が読む外部サービスへ起草した文面を投稿する前には`agent-toolkit:review-external-writes`を用いる。投稿する主体が文面、投稿先、目的と根拠の所在を確定し、読み取り専用の投稿前レビュー担当へ文脈を持たない読者の観点で内容の確認を依頼する。投稿前レビュー担当は文面の採否と投稿認可を持たず、投稿主体が指摘を反映して送信する。投稿先ごとにレビュー手順を複製する案と常時規範から別の`share/`手順を読む案は、同じ判断の保守先と読込量を増やすため採用しない。
