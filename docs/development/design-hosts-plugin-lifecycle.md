# plugin導入と実体の回収の設計記録

[設計記録の索引](design.md)

## Codexのlocal plugin導入

dotfilesの`post_apply`によるローカルagent-toolkit導入は、Codex CLIを準備した後に`agent-toolkit-codex/`を生成し、マーケットプレイス登録とplugin導入をCodex公式CLIへ委譲する。
`agent-toolkit-codex/`の生成に失敗した場合は、post-applyのstep失敗として永続logへ記録し、全体を非0で終了する。
明示的に無効なpluginは導入状態とcacheを保持し、版更新も保留する。
明示的な再有効化後に通常の更新を再開する（[Codexガイド](../guide/codex-guide.md#プラグイン更新の反映)）。
CLI成功後は同コマンドで`codex plugin list --json`を取得し、版数一致と有効状態を検証する。
実際の追加・更新後に共有daemonが稼働中なら、手動の再起動を案内する。
通常の公式CLIにはdotfilesの復旧処理が介在しないため、Linuxの適用前を含め、自動停止・再起動はしない。
現行versionの導入はCodex CLIへ委ねる。
更新処理は`plugin add`の前に、通常のversionディレクトリを`CODEX_HOME`直下の固定の退避先（`.dotfiles-plugin-cache-saved/<marketplace>/<plugin>/`）へ移動する。
導入後の状態確認を含む最後のCodex CLI呼出後に、目標version以外を同じ絶対パスへ戻す。
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
これにより個別コマンドのcache副作用を前提とせず、更新関数の終了時に退避した全旧パスの存在を保証する。
Codex 0.163.0では、App Serverの`plugin/list` RPCもlocal marketplaceの新versionを自動導入し、更新関数の外で全旧cacheを削除する。
隔離対照では、新versionに対するCLIの`plugin add`も旧cacheを削除し、同versionのRPC、CLIの`plugin list --json`、daemon照会と`codex exec`は旧cacheを保持した。
この上流のcache寿命不整合は[openai/codex#31383](https://github.com/openai/codex/issues/31383)で0.142.5から0.162.0までの複数版・複数OSから報告され、live hookの再読込も[openai/codex#36605](https://github.com/openai/codex/issues/36605)などで未解決である。
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
旧versionの実体は、保存済みセッションが保持した絶対パスを再開後も使えるように、後継versionの導入から猶予期間（`plugin_cache_versions.py`の`GRACE_DAYS`、7日）を過ぎるまで更新処理が保持する。
猶予期間を過ぎた旧versionは、退避より前に更新処理が削除する（2026年11月19日のユーザー決定「Codexの古いのは消して」）。
それまでの設計は全旧versionを保持し、sthenoではagent-toolkitが37版（1.79GB、81,225ファイル）に増えていた。

- 導入時刻は各versionの`.codex-plugin/plugin.json`のmtimeとし、Claude Codeの旧版cache整理と同じ選び方（[Claude Code plugin cache](operations-plugin-cache.md)）を使う。
- 導入中のversionと目標のversionは日数によらず残す。
- 稼働中のCodex（`codex_processes.py`の分類のうち更新ループを除く）は起動時に現行だったversionを使い続ける。
  最も早く起動したCodexより後に後継が導入されたversionも残し、起動時刻を取得できないCodexがあれば削除しない。
- 削除はversionを`<version>~removing`へ名前の変更で切り離してから行う。
  Windowsで使用中のファイルを含むため移動できないversionは残し、欠けた版を元のパスに作らない。
  切り離した後の削除が途中で止まった場合は、次回の実行が`~removing`を削除する。
- 明示的に無効なpluginは削除しない。

会話の開始から再開までの間隔を観測して7日に決めた。
2026年11月19日にeuryale（Linux、10,491件）とstheno（Windows、621件）のCodexのセッション記録（`sessions/`と`archived_sessions/`のrollout）を調べた。
ファイル名の開始時刻から最終書込（mtime）までは、最長でeuryaleが3.2日、sthenoが2.2日だった。
最長の1件は75.9時間の間隔を置いて同じrolloutへ追記しており、再開した会話も同じファイルへ書く。
7日はその2倍以上の余裕を持ち、Claude Codeの旧版cache整理と同じ値である。
猶予期間を過ぎた旧versionの会話を再開すると、hookは`atk-hook`の復旧で動くが、会話の履歴が指す旧versionのskillなどの絶対パスは失われる。

シンボリックリンク方式は代替案として却下する。
Codex 0.154.0はroot直下のAgent Plugins用`plugin.json`を`.codex-plugin/plugin.json`より優先し、同一rootから導入した場合は`hooks/list`が0件となる。
また、相対シンボリックリンクを含むCodex専用wrapperを公式CLIで導入すると、snapshotには`.codex-plugin`だけが残り、リンク先のhook・skill・実行資源が含まれない。
そのため、`agent-toolkit-codex/`は全資源を通常ファイルとして生成し、公式CLIへsnapshotと版数別cacheの管理を委ねる。
再検証はCodex CLI 0.154.0で`pytools/_internal/codex_plugin_manifests_proxy_test.py`の`test_codex_0154_registers_all_hooks_independent_of_project_trust`を実行する。
この試験は、隔離した2つの`CODEX_HOME`での登録集合を確かめる。

## plugin実体の回収と稼働中の参照

更新・回収・修復でplugin実体を削除または置換する処理は、消費側が保持する絶対パスを有効なまま保つ。
稼働中のhook・skill・MCPは起動時に解決したplugin rootの絶対パスを使い続けるため、参照中に旧版を削除すると実行中の処理が`[Errno 2]`などで失敗する。
保存済みセッションはプロセスとして存在しない期間も後から再開できるため、プロセス一覧は参照の終了を表さない。
agent-toolkitの更新では旧versionを自動復旧し、Codexの稼働状態を更新可否の判定に使わない。
実体そのものを除去する処理は、参照の終了を判定できない間、既存の実体を保持して延期する。

| 処理 | 参照の判定 | 延期後の再開 |
| --- | --- | --- |
| Codexのpost-apply更新（`install_codex_plugins.py`） | 猶予期間内の全通常versionディレクトリを`CODEX_HOME`直下の退避先へ移動し、CLIの処理後に同じ絶対パスへ戻す | 同じ実行で更新と旧versionの復旧を完了する。復旧失敗は導入成功と区別して報告し、退避先を残して次回の実行で戻す |
| Codexの旧version回収（`install_codex_plugins.py`の`_prune_retired_versions`） | 後継の導入からの経過日数と、`codex_processes.py`の起動時刻（更新ループを除く最も早い起動）。Windowsでは移動できない版を使用中とみなす | 使用の終了後の`update-dotfiles`で削除する |
| 単体インストーラー（`install-claude.sh`・`install-claude.ps1`） | post-apply更新と同じ退避先へ、更新前の全通常versionディレクトリを移動する。旧versionの回収は持たない | 同じ実行で更新と旧versionの復旧を完了する。復旧失敗時は退避先と報告先を表示して非0で終わり、次回の実行で戻す |
| Claude Codeの旧版cache整理（`prune_claude_plugin_cache.py`） | `claude_plugin_references.py`。その版の配下を指すプロセスと、その版が現行だった期間に起動したClaude Codeセッション | 参照の終了後の`update-dotfiles`で削除する |
| Claude Codeの現行版cache欠落修復（`install_claude_plugins.py`） | `claude_plugin_references.py`。導入先を指すプロセスと、起動時刻を問わない稼働中のClaude Codeセッション | post-applyの`deferred`と延期記録による自動更新タイマーの再試行。タイマーを持たないホストではClaude Codeのセッションを全て終了してから`update-dotfiles`を再実行する |
| Codexの不要pluginの除去（`install_codex_plugins.py`の`_remove_unused_plugins`） | `codex_processes.py`の役割分類。`session`だけを数え、管理daemonとその補助実行体・更新ループは数えない | post-applyの`deferred`と延期記録による自動更新タイマーの再試行。タイマーを持たないホストではCodexの終了後に`update-dotfiles`を再実行する |

Claude Code 2.1.303の`claude plugin uninstall`とmarketplaceの削除は、plugin cacheの実体を孤立の標識（`.orphaned_at`）付きで残す。
他のscopeが残らないpluginでは永続データ領域（`plugins/data/<id>/`）を削除する（いずれも隔離した設定ディレクトリでの観測）。
`install_claude_plugins.py`のdeprecated pluginの除去は、実体を削除せず、データ領域も`--keep-data`で残すため、稼働中のセッションが使う実体を削除する処理に当たらず、延期を設けない。
`plugin.json`は設定項目を持たない。
このため通常の環境では稼働中のセッションのhook・MCPはデータ領域を参照せず、削除しても動作は変わらないので、この修復は参照の終了を待つ延期の対象にしない。
旧versionの保持は、ユーザーがdaemonを再起動する時期と独立している。
旧versionは移動して退避し、複製しない。
複製する方式でも、CLIが`plugin add`で旧versionを削除してから復元の複製を終えるまで元のパスから実体へ到達できない。
その区間は旧versionの総量に比例して伸びる。
移動する方式ではこの区間が`plugin add`と導入後の確認の時間に縮み、旧versionの数に依存しない。
最新版へhookを付け替える案は、旧版のskill・MCP・遅延importを守れないため採らない。
プロセス終了まで更新を延期する案は、後から再開できる保存済みセッションを判定できず、Codexを常用する環境で更新が完了しないため採らない。
稼働中のCodexの起動時刻だけで旧versionの回収を判定する案も、同じ理由で採らない。
`codex resume`で再開したプロセスは起動時刻が新しく、再開した会話の履歴が指す旧versionの絶対パスを使い続ける。
そのため起動時刻は猶予期間に加えて保持を延ばす条件にだけ使い、再開した会話は猶予期間で守る。
