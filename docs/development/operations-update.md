# 運用機能: dotfilesの更新と導入モード

## `update-dotfiles`のuv自己更新と実行環境

手動起動した`update-dotfiles`は公式インストーラー版uvの自己更新を起動時に試行する。
WindowsでMCPなどが`uvx`を使用中の場合、実行ファイルを置換できずに自己更新が失敗することがある。
この場合も`update-dotfiles`はインストール済みのuvで更新を続行し、処理の最後に失敗と次回の再試行を案内する。
`atk wi process-loop`からの起動では、常駐中の`uvx`による実行ファイル競合を避けるため自己更新を省略し、
次回の手動起動まで延期する。
補助的なuv自己更新を理由として、dotfiles更新を停止させないためである。

`update-dotfiles`の各工程は`MISE_AUTO_INSTALL=0`を与えたサブプロセスとして実行する。
実行ファイル名で起動したコマンドがmiseのshimへ解決されると、
呼び出したコマンドと無関係なツールの自動導入が実行され、その失敗が更新処理全体を止めるためである。
ツール自体はpost-apply工程で`mise install`を明示的に実行して導入するため、`MISE_AUTO_INSTALL=0`の影響を受けない。

`update-dotfiles`のgit pull工程には待ち時間の上限を設ける。
環境変数を設定しない場合は600秒とし、環境変数`UPDATE_DOTFILES_GIT_TIMEOUT_SEC`へ秒数を与えて調整する。
`0`を与えると上限を無効化する。
GitHub向けSSHのupload-pack通信が応答しない環境で、更新処理が無期限に滞留するためである。
上限を超えた場合は、git pull工程から起動した子孫プロセスを終了させる。
そのうえで、工程名と処理が未完了であることを標準エラーへ出力し、終了コード1で終わる。
子孫を終了させるのは、`git`と`ssh`が出力のハンドルを保持したまま停止すると、直接の子を終了させるだけでは待機が終わらないためである。
`chezmoi apply`工程へは上限を設けない。

CLIを`agent-toolkit/bin/atk`から`agent-toolkit/scripts/atk`へ移す前に起動した`update-dotfiles`は、pullと未コミット内容の復元の後、旧パスの不在により退避の回収で終了コード1となる場合がある。
「復元は成功しましたが退避の回収は未完了です」と表示された場合は、作業内容とindexが復元済みであることを保存refの内容と比べて確かめる。
確かめられたら、その実行が表示した完全なrefだけを`./agent-toolkit/scripts/atk worktree-stash drop <ref>`で回収し、`./bin/update-dotfiles`を再実行する。
Windowsではそれぞれ`agent-toolkit\scripts\atk.cmd`と`bin\update-dotfiles.cmd`を使う。
復元を確認できない場合はrefを保持して原因を調べる。

## dotfilesプラグインの適用と更新

dotfiles本体の`plugins/<プラグイン名>/`へ拡張を置く。
`plugins/`は本体のGit追跡対象から外れ、各プラグインは独立したGitリポジトリとしても管理できる。
導入時の配置例は[README.md](../../README.md)の設定の説明を参照する。

`chezmoi apply`の本体後処理が完了した後、プラグインをディレクトリ名順に適用する。
各プラグインでは`.chezmoi-source/`があればchezmoiで強制適用し、続いて`post_apply.py`があればuvで実行する。
後処理の作業ディレクトリは各プラグインのルートとし、chezmoiの永続状態もプラグインごとに分離する。
本体と配布先が重なる場合は警告を表示し、プラグインの内容で上書きする。
`plugins/`が無い場合と空の場合は変更なしで終了する。

プラグインのapplyに失敗した場合は、そのプラグインの`post_apply.py`を省略する。
他のプラグインの適用は続け、applyと後処理の失敗を集約して更新結果へ返す。
失敗したプラグインの配布物・スクリプトとコマンド出力を確認し、原因を解消して`update-dotfiles`を再実行する。

`update-dotfiles`は本体のpull後、chezmoiの初期化・適用前に、Git管理されたプラグインを名前順でpullする。
Git管理されていないプラグインはそのまま適用する。
pullの失敗とタイムアウトでは以後の更新を停止し、プラグイン名と出力を更新ログへ残す。
待ち時間の上限は本体と同じ`UPDATE_DOTFILES_GIT_TIMEOUT_SEC`を使う。

## 導入モードと不要ツール・キャッシュの整理

`agent_toolkit._common.dotfiles_mode`が初回導入前から使える標準ライブラリだけの設定取得機能を提供する。
保存先は`atk config`と同じ`config.json`、キーは`dotfiles_mode`、値は`full`と`minimal`、既定値は`full`である。
`install.sh --mode`・`install.ps1 -Mode`・`update-dotfiles --mode`・`dotfiles-post-apply --mode`は指定値を保存し、`AGENT_TOOLKIT_CONFIG_DOTFILES_MODE`で後続のテンプレートと後処理へ渡す。
引数を省略した実行は環境変数、保存値、既定値の順に解決する。
`atk config get dotfiles_mode`と`atk config set dotfiles_mode <mode>`は共有依存環境の準備前にも使える。

配布用mise設定は`config.toml.tmpl`がモードに応じて生成する。
actionlint・editorconfig-checker・git-cliff・pinact・shellcheck・shfmt・typosはリポジトリの`mise.toml`と`mise.lock`が保持する。
`make update-mise-locks`は`update_global_mise.py`でfullの設定を一時展開し、配布用lockとテンプレート内のdotnet版を更新する。
実行ホストの保存済みモードは変更しない。

`post_apply._StepSpec.full_only`の工程はminimalで実行しない。
対象には各AI CLI・プラグイン・MCP準備・statuslineと、AI設定・リンクの同期が含まれる。
mise・シェル基本ツール・tmux・pytools・dotfilesプラグインの反映は維持する。
minimalのmise操作はホームを実行位置とし、作業ツリーの開発ツールとglobal nodeの追加を省く。

`update-dotfiles`の成功後は実効モードがminimalの場合と明示的に`--prune`を指定した場合に、不要ツールとキャッシュを整理する。
通常のfull更新では導入済み実体を保持する。
更新ランチャーはuvの外から`dotfiles-post-apply --prune-only-if-minimal`へ元の`--mode`・`--prune`引数を渡し、post-applyが実効モードを解決して整理の要否を決める。
`update-dotfiles --prune`は`DOTFILES_PRUNE=1`も後処理へ渡す。
`dotfiles-post-apply --prune`も同じ削除工程を、他の全工程が完了した後に実行する。
対象は現行のmise定義とモードから求める。
fullでは作業ツリーが引き続き参照する開発ツールを保持する。
minimalでは大型ランタイム・SDK・AI関連と作業ツリーの全ツールを除去し、`mise prune -y`・`mise reshim --force`・`mise cache clear`・`uv cache clean`を実行する。
rustup・cargo・`.dotnet`は設定とユーザー導入物を残し、ランタイムとキャッシュの子ディレクトリを整理する。
mise専用のdotnet-rootも削除する。
Claude CodeとCodexの設定・会話履歴は削除対象に含めない。
Codexの管理daemonは公開CLIで停止し、通常セッションや対象実体を使うプロセスがあれば削除を開始せず、理由と再実行方法を返す。
`atk config set dotfiles_mode minimal`による設定保存だけでは削除せず、次の`update-dotfiles`成功後に整理する。
