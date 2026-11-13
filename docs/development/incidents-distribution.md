# 障害・欠陥の記録: 配布と更新に伴う不整合

[障害事例集の索引](incidents.md)に区分の定義と事例の書式を示す。

## 配布と更新に伴う不整合

- 2026年8月: 対話シェルの入れ子でPATHが384要素まで肥大していた。
  直接原因: 重複判定のないPATH追記の累積。
  対策: 重複判定付きヘルパーへの統一

- 2026年8月: ログインシェル起動が約13秒かかっていた。
  直接原因: 起動時処理がキューの`adopted`または`rejected`の項目2300件超を毎回全件パースしていた。
  対策: 参照範囲の限定で約0.66秒へ短縮

- 2026年9月5日と9月6日: sthenoで`update-dotfiles`がGitHub向けSSHのupload-pack通信で停止し、完了しなかった。
  直接原因: 応答が返らない可能性のある外部通信を、待ち時間の上限も進捗表示も持たずに同期実行していた。
  対策: git pull工程へ待ち時間の上限を設ける。
  上限を超えた場合はgit pull工程から起動した子孫プロセスを終了させ、工程名を添えてgit pullが完了しなかったことを出力して終了する

- 2026年9月6日: sthenoのpost-applyで、CodexのClaude MCP登録削除工程が`mise ERROR No version is set for shim: codex`で失敗した。
  直接原因: 呼び出し側が実行ファイル名で`codex`を起動し、PATH上のmise shimが実体を覆っていた。
  対策: mise shimのディレクトリを除外して実体を解決する共通処理を設ける。
  `pytools/`配下で該当するコマンドを起動する箇所は、この共通処理を使う

- 2026年9月6日: sthenoのpost-applyで、Codexリンクの同期とユーザーPATHの整理が`OSError: [WinError 448]`で失敗し、残りの対象を処理しなかった。
  直接原因: 複数のパスを順に調べる処理が、個々の確認で送出される`OSError`を処理していなかった。
  対策: 確認できなかったパスを警告として報告し、同じ処理の残りの対象を継続して調べる

- 2026年9月13日: `update-dotfiles`がmarketplaceキャッシュを更新した直後のClaude Codeセッションで、agent-toolkitのスキル、hookおよびMCPが全て読み込まれなかった。
  別のWindows環境では、未コミット内容の退避時に`atk`を解決できず`FileNotFoundError: [WinError 2]`でstage 1が停止した。
  直接原因: marketplaceのremove/add前に取得した導入一覧を修復後も再利用し、plugin installの終了コードを導入・有効化実体の代わりに完了根拠とした。
  Windowsの退避処理は同じ配布物のatkランチャーを絶対解決せず、PATHによる名前解決を使った。
  加えてupdate-dotfilesとpost-applyが永続ログを持たず、過去実行の分岐を再構成できなかった。
  対策: marketplace修復後に導入一覧を再読し、非TTY installと導入・有効化実体の事後検証を完了条件にする。
  退避はOS別ランチャーを絶対パスで起動し、起動不能を文脈付き非0結果にする。
  全段とpost-applyステップの開始、終了、所要時間、結果を、機密本文を除外したサイズ制限付き永続ログへ記録する

- 2026年9月26日: euryaleで`atk --help`の起動に約2.9秒かかった。
  PATHの先頭側にある`~/.local/bin/atk`が作業ツリー版`~/dotfiles/agent-toolkit/bin/atk`を覆い隠し、プラグインキャッシュ全体を`find`で走査してから実体を起動していた。
  同ファイルは2026年7月26日に`atk serve`常駐用ランチャーとして生成され、翌日の改名から2か月間残存した。
  直接原因: 2026年7月26日「feat(agent-toolkit): フィードバック管理Web UIを追加する」、15c2e214が`~/.local/bin/atk`へランチャーを生成した。
  2026年7月27日「fix(dotfiles): atk serve常駐サービスのuv解決を絶対パス化し起動確認を追加する」、1116f984で`~/.local/bin/atk-serve`へ改名した。
  その際、旧名の削除を`post_apply`の旧配布物削除へ加えなかった。
  旧配布物削除の規定は`.chezmoi-source/`配下のファイルだけを対象とし、`post_apply`の工程が生成するファイルを含まなかった。
  同じ原因で、廃止した工程が配置した`~/.config/agent-toolkit/feedback-inbox.enabled`と`review-balance-mode.claude-heavy`も残存していた。
  対策: 3件の旧生成物（`atk.cmd`を含む）を`pytools/post_apply.py`の`_REMOVED_PATHS`へ登録する。
  `pytools-edit`スキルの実装規約へ、工程が配置するファイルの配置先を改名または工程を廃止する場合に旧パスを登録する規定を加える

- 2026年9月30日: Windowsホストで`actionlint`が`command not found`（終了コード127）となり、`mise which actionlint`は「No executable found for configured tool」で終了コード1を返した。
  同ホストの`installs/actionlint/1.7.12/`は2026年6月8日の導入で、`.mise-bins`ディレクトリが無かった。
  直接原因: 全体設定と`mise.toml`へ2026年9月20日（aa9cbec18）に加えた`[tools.actionlint] symlink_bins = true`が、導入済みのホストへ反映されなかった。
  miseは導入時にだけ配置を生成し、`mise install`は導入済みの版を再導入しないためである。
  設定コメントはローカル環境の強制再インストールを手作業として定めていたが、各ホストで行われなかった。
  同じ原因で、2026年9月8日の7c3ba2363が`symlink_bins`を加えたときも、pre-commitとCIのactionlintの実行が止まった。
  対策: `pytools/_internal/setup_mise.py`の後処理が`mise install`の後に`mise bin-paths`の配置先の欠落を検出し、欠落したツールを同じ版で`mise install --force`により再導入する

- 2026年9月: euryaleの`dotfiles-autoupdate.service`が10分ごとに起動しながら毎回1段目で失敗した。
  直接原因: systemdユーザーunitへ`Environment=PATH`が無く、`update-dotfiles`が実行ファイル名で起動する`chezmoi`を解決できなかった。
  対策: unit生成側でPATHを明示し、`~/.local/bin`とmiseのshimsをsystemdが設定するPATHの前へ置く

- 2026年9月: Windowsのpytools再導入が2026年8月10日以降毎回延期され、`sonnet`・`opus`・`fable`が導入されなかった。
  直接原因: 常駐プロセスの復元可否分類を正規の起動手順が生成しないプロセス属性で記述し、延期を終了コード0の通常出力で終了していた。
  対策: 分類条件を正規の起動手順から導出し、延期と失敗を`update-dotfiles`の最終出力へ表示する

- 2026年10月21日: Codex pluginを2.222.3へ更新した後、実行中セッションが参照する2.222.2のcache rootが消え、PreToolUse hookが全操作を遮断した。
  直接原因: pre-commitの生成処理が登録済みlocal marketplace sourceを新versionへ更新し、別のCodex App Serverが`plugin/list` RPCで新versionを自動導入して全旧cacheを削除した。
  この処理は`install_codex_plugins.run`の退避・復旧をしなかった。
  背景原因: （作り込み要因）外部コマンドとApp Serverの副作用を実機で確かめず、提供元Issueと上流実装も調べないまま、cacheを削除する主体を明示的な`plugin add`と更新関数内の後続呼出しへ限定した。
  （見逃し要因）既存試験は内部`run()`と単体インストーラーへ削除を注入し、`git commit`、生成処理、別App Serverの自動導入、live hook消費を一続きで通さなかった。
  （根本原因）外部挙動を確認しない設計と内部単位だけの試験を、実用シナリオ全体の保証として受理できた。
  調査の経過: Codex 0.163.0の隔離対照では、新versionに対するApp Serverの`plugin/list` RPCとCLIの`plugin add`が旧cacheを削除した。
  同versionのRPC、CLIの`plugin list --json`、daemon照会と`codex exec`は旧cacheを保持した。
  [openai/codex#31383](https://github.com/openai/codex/issues/31383)は0.142.5から0.162.0までの複数環境で同じcache寿命不整合を報告している。
  対策: 更新処理内の全旧version退避・復旧を維持する。
  dotfiles向けの生成済みhookはcache外の`atk-hook`から起動し、旧`PLUGIN_ROOT`欠落時はClaude Code側の同版cacheを同じ絶対パスへ原子的に復旧する。
  同版が無い場合はClaude Codeの現行導入版からhookを起動する。
  commitと`update-dotfiles`の実用シナリオで、外部主体の削除からhook消費までを検証する

- 2026年10月25日: dotfilesの自動更新のたびに、Git管理下の`.claude/settings.json`から`enabledPlugins`のagent-basicsが消えた。
  作業ツリーとレーンworktreeに未コミット変更が残った。
  直接原因: `install_claude_plugins.py`の`_cleanup_old_project_scope`が、管理対象pluginのproject scopeの登録を全て`claude plugin uninstall --scope=project`で除去した。
  この除去でClaude Codeがプロジェクトの共有設定を書き換えた。
  背景原因: （根本原因）2026年4月のuser scope移行用の除去処理が、project scopeの登録を全て古い残骸とみなす前提のまま残った。
  2026年10月23日にdotfilesリポジトリがagent-basicsを共有設定で意図して有効化した後は、Claude Codeがセッション開始のたびに再登録する項目を、自動更新のたびに除去していた（10月25日だけで00:31、02:13、02:23、06:40、16:42に発生）。
  対策: 移行から6か月を過ぎたため除去処理を撤去し、project scopeの登録をバージョン判定から外したまま除去もしないことをテストで固定した

- 2026年11月13日: sthenoで管理Pythonの更新がアクセス拒否となり、`update-dotfiles`の本体へ進まなかった。
  直接原因: メディアリモコンが使用中のPythonをuvが入れ替えようとして失敗し、ランチャーとjobが更新の失敗を一律に中断としていた。
  背景原因: 段の分類が本体の内側にだけあり、ランチャーへ加えた段に適用されなかった。
  既定が中断で、続行するには段ごとの処置が必要だった。
  対策: 宣言表で中断集合を固定し、未知の失敗を含めて既定を続行にする。
  中断段には回復と待機上限を設け、全宣言段への注入試験で分類を確かめる。
  Windowsの使用中判定をpost-applyと共用し、復帰できる常駐だけを止めて更新する。
  同じ失敗が3回続いた場合は、未完了AWIとの重複を避けて自動起票する。
  再発（2026年11月13日）: 範囲不足・未適用。
  過去の段別修正は、新しい段と想定外の例外による停止を防げなかった。
  以下は2026年8月以降の是正commitの件名・本文による型別分類であり、各差分の再監査結果ではない。

  | 更新が止まる型 | 過去の是正 | 今回作用させる範囲 |
  | --- | --- | --- |
  | 非必須段の失敗で中断 | 8月10日のuv自己更新失敗の続行、10月19日の本体の段の分類、11月8日のPython更新の追加 | 全段の宣言と既定続行。分類の外側で段を追加しない |
  | 一時的な資源の競合・残存 | Gitの標準ロックの待機・回収、Windowsの使用中プロセスの分類 | 中断段の自己回復、管理Pythonの更新前の使用中検出 |
  | 段の無期限停止 | 退避操作の上限、9月のGitのSSH通信の上限 | 段ごとの上限と子孫回収 |
  | 外部コマンドの不在など環境差 | ps不在、systemdユーザーマネージャーの停止への対処 | 非必須段は続行。中断段は回復後に具体的な操作を案内 |
  | 更新中のランチャー置換 | 旧batchの再開位置と終了値取得の回帰試験 | 解析済み終了blockとbyte位置を保持 |
  | 文字コードの不一致 | UTF-8での出力取得 | 未知の例外の続行と全段への注入試験 |
