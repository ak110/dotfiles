# 障害・欠陥の記録: 配布と更新に伴う不整合

[障害事例集の索引](incidents.md)に区分の定義と事例の書式を示す。

## 配布と更新に伴う不整合

- 2026年9月5日と9月6日: sthenoで`update-dotfiles`がGitHub向けSSHのupload-pack通信で停止し、完了しなかった。
  直接原因: 応答が返らない可能性のある外部通信を、待ち時間の上限も進捗表示も持たずに同期実行していた。
  対策: git pull工程へ待ち時間の上限を設ける。
  上限を超えた場合はgit pull工程から起動した子孫プロセスを終了させ、工程名を添えてgit pullが完了しなかったことを出力して終了する

- 2026年9月13日: `update-dotfiles`がmarketplaceキャッシュを更新した直後のClaude Codeセッションで、agent-toolkitのスキル、hookおよびMCPが全て読み込まれなかった。
  直接原因: marketplaceのremove/add前に取得した導入一覧を修復後も再利用し、plugin installの終了コードを導入・有効化実体の代わりに完了根拠とした。
  対策: marketplace修復後に導入一覧を再読し、非TTY installと導入・有効化実体の事後検証を完了条件にする。
  全段とpost-applyステップの開始、終了、所要時間、結果を、機密本文を除外したサイズ制限付き永続ログへ記録する
- 2026年9月26日: euryaleで`atk --help`の起動に約2.9秒かかった。
  直接原因: 改名した旧名のランチャー`~/.local/bin/atk`が`post_apply`の旧配布物削除に加えられず、2か月間PATHの先頭で本体を覆い隠していた。
  旧配布物削除の規定は`.chezmoi-source/`配下のファイルだけを対象とし、`post_apply`の工程が生成するファイルを含まなかった。
  対策: 旧生成物を`_REMOVED_PATHS`へ登録し、`pytools-edit`スキルの実装規約へ、工程が配置するファイルの配置先を改名または工程を廃止する場合に旧パスを登録する規定を加える
- 2026年9月30日: Windowsホストで`actionlint`が`command not found`（終了コード127）となり、`mise which actionlint`は「No executable found for configured tool」で終了コード1を返した。
  直接原因: 全体設定と`mise.toml`へ2026年9月20日（aa9cbec18）に加えた`[tools.actionlint] symlink_bins = true`が、導入済みのホストへ反映されなかった。
  対策: `pytools/_internal/setup_mise.py`の後処理が`mise install`の後に`mise bin-paths`の配置先の欠落を検出し、欠落したツールを同じ版で`mise install --force`により再導入する

- 2026年10月21日: Codex pluginを2.222.3へ更新した後、実行中セッションが参照する2.222.2のcache rootが消え、PreToolUse hookが全操作を遮断した。
  直接原因: pre-commitの生成処理が登録済みlocal marketplace sourceを新versionへ更新し、別のCodex App Serverが`plugin/list` RPCで新versionを自動導入して全旧cacheを削除した。
  対策: 更新処理内の全旧version退避・復旧を維持する。
  dotfiles向けの生成済みhookはcache外の`atk-hook`から起動し、旧`PLUGIN_ROOT`欠落時はClaude Code側の同版cacheを同じ絶対パスへ原子的に復旧する。
  同版が無い場合はClaude Codeの現行導入版からhookを起動する

- 2026年10月25日: dotfilesの自動更新のたびに、Git管理下の`.claude/settings.json`から`enabledPlugins`のagent-basicsが消えた。
  作業ツリーとレーンworktreeに未コミット変更が残った。
  直接原因: `install_claude_plugins.py`の`_cleanup_old_project_scope`が、管理対象pluginのproject scopeの登録を全て`claude plugin uninstall --scope=project`で除去した。
  対策: 移行から6か月を過ぎたため除去処理を撤去し、project scopeの登録をバージョン判定から外したまま除去もしないことをテストで固定した

- 2026年11月13日: sthenoで管理Pythonの更新がアクセス拒否となり、`update-dotfiles`の本体へ進まなかった。
  直接原因: メディアリモコンが使用中のPythonをuvが入れ替えようとして失敗し、ランチャーとjobが更新の失敗を一律に中断としていた。
  対策: 宣言表で中断集合を固定し、未知の失敗を含めて既定を続行にする。
  中断段には回復と待機上限を設け、全宣言段への注入試験で分類を確かめる。
  Windowsの使用中判定をpost-applyと共用し、復帰できる常駐だけを止めて更新する。
  同じ失敗が3回続いた場合は、未完了AWIとの重複を避けて自動起票する。
  再発（2026年11月13日）: 範囲不足・未適用。
  過去の段別修正は、新しい段と想定外の例外による停止を防げなかった

- 2026年11月15日: sthenoのセッション記録一覧の取得が、uvの管理Python探索のos error 448で失敗した。
  直接原因: 省略版3.15のJUNCTIONを、RedirectionGuardが有効なSSHプロセスから追従できなかった。
  リンク先の具体版3.15.2は実在し、省略版の参照先も同じ実体を指していた。
  対策: ユーザーの認可を得て、管理者token・JUNCTION属性・リンク先・本体の実在を再確認し、対象リンク1個をrmdir（/Sなし）とmklink /Jで同じ実体へ再生成した。
  保護設定、レジストリ、Python本体、他リンク、PATHは維持した。
  同じSSHのuv python find 3.15と、稼働中serveの記録一覧APIが警告なしで成功した。
  再発（2026年11月19日）: 範囲不足。
  SSH配下の`update-dotfiles`が、TypeScript言語サーバーへの接続が済んだ状態でも「tsserver.jsが見つからない」と報告した。
  是正: JUNCTIONを通過せず格納先を読み、参照先の実体でtsserver.jsを確かめる

- 2026年11月18日: sthenoの`~/.claude/settings.json`に自動圧縮の上書きが残り、会話が約45万トークンに達するたびに圧縮されていた。
  直接原因: 配布元の`share/claude_settings_json_managed.json`から2つのキーを削除した2026年9月13日のcommitが、`_REMOVED_ENV_KEYS`へキーを登録しなかった。
  配布処理は`env`を再帰マージするため、配布元から削除したキーは配布先に残る。
  対策: 2つのキーを`_REMOVED_ENV_KEYS`へ登録し、次の配布で除去する
