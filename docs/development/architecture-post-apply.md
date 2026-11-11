# アーキテクチャ: post-applyとPython依存環境

## agent-toolkitのホスト別配布

### `post_apply.py`の工程間の順序

- dotfilesプラグインの適用は`pytools/_internal/apply_dotfiles_plugins.py`が担い、本体の後処理が終わってから実行する。
  プラグインごとのchezmoi永続状態を本体から分離し、本体と配布先が重なる場合は警告してプラグインの内容を上書きする。
  配置と失敗時の扱いは[operations-update.md](operations-update.md#dotfilesプラグインの適用と更新)の「dotfilesプラグインの適用と更新」を参照
- `post_apply.py`は互いの成果を使わない工程を同時に実行し、工程間の順序を`_StepSpec`の先行工程の宣言で保つ。
  リンク同期、Claude Code plugin、Codex plugin、旧User scope MCPの移行の順序もこの宣言で保つ。
  先行工程を宣言する対象は、同じ資源を扱う工程の組と、先行工程が導入する実行ファイルを使う工程とする。
  資源は設定ファイルの読み書き、プロセスとユーザーのPATH、npmとmiseの管理領域、plugin cache、Claude Code pluginの複製元である`agent-toolkit/`、依存環境を保存するデータ領域、systemd、codexプロセスの稼働判定を指す。
  子プロセスやサービスの再起動を経由して間接的に書き換える資源も含める。
  例えば`atk serve`工程が再起動したサービスは`scripts/launch.py`を経てデータ領域の依存環境を同期する。
  工程を追加する場合も同じ基準で宣言する
- 工程の同時実行は、2026年11月11日の測定を根拠に維持している。
  同じ版（`6ec5050fa`）の後処理を同じホストで、現行の同時実行と、同時に動く工程を1つに限った実行（`ThreadPoolExecutor`の`max_workers`を1にした測定用の起動）とで交互に実行した。
  CLIとプラグインの更新が無く、agents_serverのMCP起動確認が省略された回どうしでは、同時実行が20.1秒、17.3秒、23.7秒（3回）、1工程ずつの実行が36.0秒、31.4秒、35.1秒、45.5秒、36.0秒（5回）だった（load averageは19〜25）。
  同時実行の回はどれも1工程ずつの回より短く、最も近い組でも7.7秒の差がある。
  平均の差（16.4秒）は同じ条件の回の幅（同時実行6.4秒、1工程ずつ14.1秒）を超える。
  MCP起動確認を含む回の同時実行は40.0秒、48.9秒、65.6秒、42.5秒で、同じ入力の1工程ずつの対照は取れていない。
  1回目（40.0秒）の工程ごとの所要の合計は約62秒であり、同時実行による短縮は約22秒と推定する（工程ごとの所要は同時実行中の競合を含むため、推定である）。
  1工程ずつの70.6秒の回は、load averageが43〜46へ上がっていたため、比較から外した。
  実行中の工程の管理、工程ごとの出力の取り置き、列挙順の出力待ち、PATHとnpmのロックは、いずれも同時実行のもとで出力の順序と資源の排他を保つために使っており、除いた部分は無い
- `post_apply.py`の工程のうち、HOMEで解決されない実機の共有資源（systemdのユーザーマネージャー、`/dev/shm`）を操作する工程には`_StepSpec`の`host_resources`を付ける。
  HOMEが実行ユーザーのパスワードデータベース上のホームと異なる実行（手動観測やテスト）では、この工程を実行せず成功かつ変更なしとし、理由を画面へ出力する。
  `systemctl --user`は`XDG_RUNTIME_DIR`とD-Busで実機のユーザーマネージャーへ接続するため、HOMEの差し替えでは隔離できない。
  工程を追加する場合も同じ基準で付ける
- `post_apply.py`は工程ごとの出力を列挙順にまとめて画面へ表示する
- 進捗の表示は、標準エラーが端末でエージェント環境でない場合（`is_human_terminal()`が真）に限り、標準エラーの1行を書き換える形で出す。
  パイプへの出力とエージェント環境では進捗を出さない
- 工程の例外は「工程名: 失敗: 例外の型: メッセージ」の1行だけを画面へ出力し、Tracebackは永続ログに残す。
  工程が`PostApplyOutcome`の`next_action`を返した失敗は、その工程の行に工程固有の次の操作を出す
- 実行全体の失敗後の次の操作と永続ログの所在は1回だけ出力する。
  環境変数`UPDATE_DOTFILES_RUN_ID`がある`update-dotfiles`経由の実行では、`update-dotfiles`の終了時に出す。
  この環境変数が無い`dotfiles-post-apply`の単独実行では、post-applyの集計の行（`完了: 更新 N 件 / スキップ N 件 / 失敗 N 件`）の後に出す
- 開始行、ロガー`httpx`のINFO、`claude` CLIの実行記録、実行中のOSを対象外とする工程の行は永続ログ（`update-dotfiles.log`）にだけ残す

### Python依存環境の共有

`plugin_environment.py`は選択したPythonの実行ファイルパス、OS・アーキテクチャ、依存導入に作用するpyproject設定と`uv.lock`を正規化して共有環境を識別する。
配布root、プラグイン版、文書・コード・lint設定の差は環境を分ける条件に含めない。
依存だけを同期し、起動ごとのrootを`PYTHONPATH`の先頭へ置いて、その配布版のコードと資源を参照する。
pyfltr MCPも`_common/pyfltr_entry.py`からlockfileで準備したconsole entry pointへ接続する。
この境界でplugin専用のuv環境指定を解除し、pyfltrがチェック対象のリポジトリの依存環境を使えるようにする。

準備には`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`を実行する。

`--prepare`の後ろのtargetは配布root内に存在する起動対象を確認する必須引数であり、共有環境の選択には影響しない。
上記の組み合わせが未準備の場合に実行し、同じ組み合わせなら別の配布rootやworktreeでも準備済み環境を共有する。
別worktreeを指定する場合の`<plugin root>`は、そのworktree内の`agent-toolkit`の絶対パスに置き換える。
rootは`launch.py`の配置または`--project`から解決され、cwdの変更だけでは切り替わらない。

`update-dotfiles`の後処理は両ホストの実際の有効版と作業ツリー版を列挙し、公開インストーラーも同じ準備処理を使う。
通常のCLI・hook・MCPは準備済み環境のPythonを直接起動するため、起動のたびに依存を照会しない。
未準備時の診断は`update-dotfiles`または公開インストーラーでの再準備を案内する。
更新後は新規起動が新しい依存キーへ切り替わり、稼働中の旧版が使う既存環境を保持する。
共有化の更新には旧環境の自動回収を加えない。
minimalの通常更新と明示的な`--prune`は、使用中保護の判定後にAI用環境を削除する。
