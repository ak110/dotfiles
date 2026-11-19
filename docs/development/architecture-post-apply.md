# アーキテクチャ: post-applyとPython依存環境

## `post_apply.py`の工程間の順序

- dotfilesプラグインの適用は本体の後処理が終わってから実行する。
  配置と失敗時の扱いは[operations-update.md](operations-update.md#dotfilesプラグインの適用と更新)の「dotfilesプラグインの適用と更新」を参照
- `post_apply.py`は互いの成果を使わない工程を同時に実行し、工程間の順序を`_StepSpec`の先行工程の宣言で保つ。
  リンク同期、Claude Code plugin、Codex plugin、旧User scope MCPの移行の順序もこの宣言で保つ。
  先行工程を宣言する対象は、同じ資源を扱う工程の組と、先行工程が導入する実行ファイルを使う工程とする。
  資源は設定ファイルの読み書き、プロセスとユーザーのPATH、npmとmiseの管理領域、plugin cache、Claude Code pluginの複製元である`agent-toolkit/`、依存環境を保存するデータ領域、systemd、codexプロセスの稼働判定を指す。
  子プロセスやサービスの再起動を経由して間接的に書き換える資源も含める。
  例えば`atk serve`工程が再起動したサービスは`scripts/launch.py`を経てデータ領域の依存環境を同期する。
  工程を追加する場合も同じ基準で宣言する
- 工程の同時実行は、2026年11月11日の測定を根拠に維持している。
  CLIとプラグインの更新が無く、agents_serverのMCP起動確認が省略された回どうしでは、同時実行が17.3〜23.7秒（3回）、1工程ずつの実行が31.4〜45.5秒（5回）で、最も近い組でも7.7秒の差があった（同じ版・同じホスト、load averageは19〜25）。
  実行中の工程の管理、工程ごとの出力の取り置き、列挙順の出力待ち、PATHとnpmのロックは、いずれも同時実行のもとで出力の順序と資源の排他を保つために使っており、除いた部分は無い
- `post_apply.py`の工程のうち、HOMEで解決されない実機の共有資源（systemdのユーザーマネージャー、`/dev/shm`）を操作する工程には`_StepSpec`の`host_resources`を付ける。
  HOMEが実行ユーザーのパスワードデータベース上のホームと異なる実行（手動観測やテスト）では、この工程を実行せず成功かつ変更なしとし、理由を画面へ出力する。
  `systemctl --user`は`XDG_RUNTIME_DIR`とD-Busで実機のユーザーマネージャーへ接続するため、HOMEの差し替えでは隔離できない。
  工程を追加する場合も同じ基準で付ける

## Python依存環境の共有

`plugin_environment.py`は選択したPythonの実行ファイルパス、OS・アーキテクチャ、依存の固定内容（`scripts/launch.py.lock`）を正規化して共有環境を識別する。
配布root、プラグイン版、文書・コード・lint設定の差は環境を分ける条件に含めない。
依存だけを同期し、起動ごとのrootを`PYTHONPATH`の先頭へ置いて、その配布版のコードと資源を参照する。
pyfltr MCPも`_common/pyfltr_entry.py`からlockfileで準備したconsole entry pointへ接続する。
この境界でplugin専用のuv環境指定を解除し、pyfltrがチェック対象のリポジトリの依存環境を使えるようにする。

準備には`uv run --no-cache --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`を実行する。

`--prepare`の後ろのtargetは配布root内に存在する起動対象を確認する必須引数であり、共有環境の選択には影響しない。
上記の組み合わせが未準備の場合に実行し、同じ組み合わせなら別の配布rootやworktreeでも準備済み環境を共有する。
別worktreeを指定する場合の`<plugin root>`は、そのworktree内の`agent-toolkit`の絶対パスに置き換える。
更新後は新規起動が新しい依存キーへ切り替わり、稼働中の旧版が使う既存環境を保持する。
fullの更新は、準備した有効版が使う環境、稼働中のプロセスが参照する環境、構築中の環境を残して旧環境を回収する。
依存の固定内容が`uv.lock`だった旧形式の版は、現行の起動部分では使う共有環境を特定できない。
そのため、Claude CodeかCodexの登録が旧形式の版を指す間は共有環境を回収せず、登録が新しい版へ移った後の更新で回収する。
