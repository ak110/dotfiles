# plugin導入と実体の回収の設計記録

[設計記録の索引](design.md)から参照する、plugin導入と実体の回収の記録である。

## Codexのlocal plugin導入

dotfilesの`post_apply`によるローカルagent-toolkit導入は、Codex CLIを準備した後に`agent-toolkit-codex/`を生成し、マーケットプレイス登録とplugin導入をCodex公式CLIへ委譲する。
`agent-toolkit-codex/`の生成に失敗した場合は、post-applyのstep失敗として永続logへ記録し、全体を非0で終了する。
`install_codex_plugins.py`は原本manifestの版数と`codex plugin list --json`の導入状態を比較し、未導入か、有効なpluginの版数不一致の場合に`codex plugin add <plugin-id>`を実行する。
明示的に無効なpluginは導入状態とcacheを保持し、版更新も保留する。
明示的な再有効化後に通常の更新を再開する（[Codexガイド](../guide/codex-guide.md#プラグイン更新の反映)）。
CLI成功後は同コマンドで`codex plugin list --json`を取得し、版数一致と有効状態を検証する。
実際のaddまたはupdateとhook状態の確認を完了した後、daemonが稼働中であれば再起動方針を1回だけ適用する。
利用セッションがあっても`codex app-server daemon restart`を自動実行し、同じ会話と子孫の継続は[Codexの起動管理とdaemon復旧](design-hosts-codex-runtime.md#codexの起動管理とdaemon復旧)へ委ねる。
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
更新処理は`plugin add`の前に、通常のversionディレクトリを`CODEX_HOME`直下の固定の退避先（`.dotfiles-plugin-cache-saved/<marketplace>/<plugin>/`）へ移動する。
導入後の状態確認とdaemon処理を含む最後のCodex CLI呼出後に、目標version以外を同じ絶対パスへ戻す。
退避先とcacheが同じボリュームにある通常の構成では、退避と復元はversionごとの名前の変更で済み、旧versionの数とファイル数に比例する複製・削除を伴わない。
`plugins`や`plugins/cache`が別ボリュームへのリンク・マウント・junctionの配下にある構成も許容し、名前の変更が失敗したversionは複製で退避・復元する。
複製で戻す場合は元のパスの隣（`<version>~restoring`）で複製を仕上げてから名前の変更で元のパスへ置き、その後に退避を削除するため、途中で失敗しても欠けた版を元のパスに残さない。
目標versionと、Windowsで使用中のファイルを含むなどの理由で移動できないversionは元の場所に残して複製し、元の実体はCLIの処理に任せる。
使用中で移動できないversionには退避先へ`<version>~copied`の印を置く。
Windowsでは使用中のファイルとそれを含むディレクトリを削除できないため、CLIの削除は元のパスにそれらを残して途中で止まる。
印のあるversionの復元は元のパスを置き換えず、元のパスに無いファイルとディレクトリだけを退避した内容から補う。
各ファイルは隣（`<name>~restoring`）で複製を仕上げてから名前の変更で置くため、途中で止まっても欠けたファイルを元の名前で残さない。
2026年11月19日のsthenoでは、agent-toolkit 2.272.0がファイル0件のディレクトリとして元のパスに残っていた。
ディレクトリの更新時刻が2.272.2の導入と同じ07:13であり、補完の無い復元が残した版と推定する。
移動先と同じversionが元のパスに再作成されていた場合（`atk-hook`の復旧など）は、再作成された実体を残して退避した側を削除する。
`plugin add`の失敗後の巻き戻しは元のパスの内容を退避した内容で置き換える（`~copied`の印のあるversionは置き換えずに補う）。
元のパスの削除が途中で失敗した場合に備えて、削除の前に退避先へ`<version>~replace`の印を置き、置き換えを終えてから消す。
印の残るversionは、次回の実行も途中まで消えた元のパスより退避した内容を優先して置き換える。
版を元のパスへ戻した直後の中断で退避の無い印だけが残った場合や、`~restoring`の複製が残った場合は、次回の実行がそれらを消す。
`~`を含む名前は版として退避しない。
強制終了などで復元前に中断した場合は退避先が残り、次回の更新処理が`plugin add`の判定より前に同じ規則で戻す。
退避から復旧までを1つの保護区間とし、復旧後はCodexを使う処理へ戻らず更新関数を終了する。
これにより個別コマンドのcache副作用を前提とせず、更新関数の終了時に既存セッションが参照する全旧パスの存在を保証する。
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
Claude Code側も不要な外部`compact-plus@compact-plus`を除去する。
圧縮後のユーザー発話の復元はagent-toolkit自身のhookが担う。
post-apply案内とWindowsのjunction除去処理は維持する。
cache version台帳、POSIXの原本接続、Windowsの原本接続とファイル同期は設けない。
旧versionの実体は、保存済みセッションが保持した絶対パスを再開後も使えるように更新処理が保持する。

シンボリックリンク方式は代替案として却下する。
Codex 0.154.0はroot直下のAgent Plugins用`plugin.json`を`.codex-plugin/plugin.json`より優先し、同一rootから導入した場合は`hooks/list`が0件となる。
また、相対シンボリックリンクを含むCodex専用wrapperを公式CLIで導入すると、snapshotには`.codex-plugin`だけが残り、リンク先のhook・skill・実行資源が含まれない。
そのため、`agent-toolkit-codex/`は全資源を通常ファイルとして生成し、公式CLIへsnapshotと版数別cacheの管理を委ねる。
検証条件と再検証手順は「[docs/development/design-hosts-plugin-lifecycle.md：Claude CodeとCodexの規範配置：2026年9月13日](audit-records-codex.md#docsdevelopmentdesign-hosts-plugin-lifecyclemdclaude-codeとcodexの規範配置2026年9月13日)」を参照する。

## plugin実体の回収と稼働中の参照

更新・回収・修復でplugin実体を削除または置換する処理は、消費側が保持する絶対パスを有効なまま保つ。
稼働中のhook・skill・MCPは起動時に解決したplugin rootの絶対パスを使い続けるため、参照中に旧版を削除すると実行中の処理が`[Errno 2]`などで失敗する。
保存済みセッションはプロセスとして存在しない期間も後から再開できるため、プロセス一覧は参照の終了を表さない。
agent-toolkitの更新では旧versionを自動復旧し、Codexの稼働状態を更新可否の判定に使わない。
実体そのものを除去する処理は、参照の終了を判定できない間、既存の実体を保持して延期する。

| 処理 | 参照の判定 | 延期後の再開 |
| --- | --- | --- |
| Codexのpost-apply更新（`install_codex_plugins.py`） | 更新前の全通常versionディレクトリを`CODEX_HOME`直下の退避先へ移動し、CLIの処理後に同じ絶対パスへ戻す | 同じ実行で更新と旧versionの復旧を完了する。復旧失敗は導入成功と区別して報告し、退避先を残して次回の実行で戻す |
| 単体インストーラー（`install-claude.sh`・`install-claude.ps1`） | post-apply更新と同じ退避先へ、更新前の全通常versionディレクトリを移動する | 同じ実行で更新と旧versionの復旧を完了する。復旧失敗時は退避先と報告先を表示して非0で終わり、次回の実行で戻す |
| Claude Codeの旧版cache整理（`prune_claude_plugin_cache.py`） | `claude_plugin_references.py`。その版の配下を指すプロセスと、その版が現行だった期間に起動したClaude Codeセッション | 参照の終了後の`update-dotfiles`で削除する |
| Claude Codeの現行版cache欠落修復（`install_claude_plugins.py`） | `claude_plugin_references.py`。導入先を指すプロセスと、起動時刻を問わない稼働中のClaude Codeセッション | post-applyの`deferred`と延期記録による自動更新タイマーの再試行。タイマーを持たないホストではClaude Codeのセッションを全て終了してから`update-dotfiles`を再実行する |
| Codexの不要pluginの除去（`install_codex_plugins.py`の`_remove_unused_plugins`） | `codex_processes.py`の役割分類。`session`だけを数え、管理daemonとその補助実行体・更新ループは数えない | post-applyの`deferred`と延期記録による自動更新タイマーの再試行。タイマーを持たないホストではCodexの終了後に`update-dotfiles`を再実行する |

Claude Code 2.1.303の`claude plugin uninstall`とmarketplaceの削除は、plugin cacheの実体を孤立の標識（`.orphaned_at`）付きで残す。
他のscopeが残らないpluginでは永続データ領域（`plugins/data/<id>/`）を削除する（いずれも隔離した設定ディレクトリでの観測）。
`install_claude_plugins.py`のdeprecated pluginの除去は、実体を削除せず、データ領域も`--keep-data`で残すため、稼働中のセッションが使う実体を削除する処理に当たらず、延期を設けない。
`claude_marketplace.py`の登録修復は、marketplaceの削除で収録pluginを除去してデータ領域を削除し、同じ実行で再導入する。
収録するagent-toolkitの`plugin_environment.py`の`runtime_data`はユーザーデータ領域（Linuxでは`XDG_DATA_HOME`配下の`agent-toolkit`）を先に選び、そこへ書き込めない場合だけデータ領域（`CLAUDE_PLUGIN_DATA`）を使う。
`plugin.json`は設定項目を持たない。
このため通常の環境では稼働中のセッションのhook・MCPはデータ領域を参照せず、削除しても動作は変わらないので、この修復は参照の終了を待つ延期の対象にしない。
daemon自動再起動と旧versionの保持は独立している。
旧versionは移動して退避し、複製しない。
複製する方式でも、CLIが`plugin add`で旧versionを削除してから復元の複製を終えるまで元のパスから実体へ到達できない。
その区間は旧versionの総量に比例して伸びる。
移動する方式ではこの区間が`plugin add`と導入後の確認の時間に縮み、旧versionの数に依存しない。
sthenoの34版（約7.6万ファイル、約1.6GiB）を実際のCodex CLIで更新した計測では、更新関数の所要が中央値165秒から14秒に、元のパスから到達できない区間が91秒から14秒に縮んだ（各5回、2026年11月18日）。
最新版へhookを付け替える案は、旧版のskill・MCP・遅延importを守れないため採らない。
プロセス終了まで更新を延期する案は、後から再開できる保存済みセッションを判定できず、Codexを常用する環境で更新が完了しないため採らない。
稼働中のCodexの起動時刻より前に置き換わった旧versionを回収する案も、同じ理由で採らない。
`codex resume`で再開したプロセスは起動時刻が新しく、再開した会話の履歴が指す旧versionの絶対パスを使い続ける。
