# アーキテクチャ

本リポジトリはchezmoi管理のdotfilesリポジトリ。
主要なコンポーネントとその役割を以下に示す。

- `.chezmoiroot`でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離する
- `.chezmoi-source/`内がchezmoiのソースディレクトリ（`dot_`プレフィックス→`~/.*`にデプロイ）
- `.chezmoi-source/dot_claude/`: Claude Code用のユーザー設定。`~/.claude/`へデプロイする
- `.chezmoi-source/dot_codex/`: Codex用のユーザー設定。`~/.codex/`へデプロイする
- `.chezmoi-source/dot_gemini/`: Antigravity CLI用のユーザー設定（`GEMINI.md`と`antigravity-cli/skills/`）。`~/.gemini/`へデプロイする
- `agent-toolkit/`: Agent Plugins、Claude Code、Codexが共有するagent-toolkitのプラグインルート（「agent-toolkitの3形式配布」を参照）
- `agent-toolkit-codex/`: `agent-toolkit/`から生成するCodex向けプラグインルート。Gitで追跡せず、`update-dotfiles`のpost-applyが生成する
- `bin/`: ユーザーのPATHへ追加して使うコマンド（「対象環境とスクリプトの配置」を参照）
- `completions/`: 事前生成したbash補完スクリプト（「bash補完（`completions/`）」を参照）
- `docs/`: エンドユーザー向けガイド（`docs/guide/`）と開発者向け文書（`docs/development/`）
- `.claude/`: 本リポジトリの開発で使うClaude Codeのプロジェクト設定とプロジェクトスキル（配布対象外）
- `.claude-plugin/`: agent-toolkitを配布するマーケットプレイスの定義（`marketplace.json`）
- `.github/`: GitHub ActionsのワークフローとCIで使う共有アクション
- `pytools/`: Pythonコマンドラインツール群（`uv tool install`でインストール）
- `rust/`: Rust製コマンドラインツール群（CIでビルドしGitHub Releaseへ配布）
- `scripts/`: 開発とCIで使うスクリプトの置き場（prek・Makefile・pyfltr・CIから呼ぶ。エンドユーザー環境では実行しない）
- `libexec/`: エンドユーザー環境（LinuxとWindows）で他のプログラムから起動される実行ファイルの置き場（`bin/`のランチャー、systemd unit、Claude Codeのhook定義などが起動する）
- `share/`: エンドユーザー環境の処理が読むデータの置き場（Claude Code・Codexの管理対象設定、ユーザー環境変数など）
- テンプレートからリポジトリルートのファイルを参照する場合は`{{ .chezmoi.workingTree }}`を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

## 対象環境とスクリプトの配置

本dotfilesは以下の二者を想定している。配布対象と開発対象でサポート範囲が異なるため、ファイル追加時にどちら用かを確認。

- エンドユーザー: Linux+Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/
  chezmoi管理ファイルはすべて両OS対応とする）
- 開発者: Linuxのみ（`make test`/prek/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように分ける。

- `scripts/`: prek・Makefile・pyfltr・CIなど開発とCIの工程から呼ばれるスクリプトの置き場
  - chezmoiで配布せず、エンドユーザー環境では実行しない。Linux前提で書いてよいが、CIのWindows jobで動くもの（`scripts/check_update_dotfiles_upgrade.py`）はWindowsでも動く書き方とする
  - 例: `scripts/check-templates.sh`・`scripts/check-cmd-encoding.sh`・
    `scripts/check-ps1-bom.sh`・`scripts/run-psscriptanalyzer.sh`
- `libexec/`: `bin/`のランチャー、chezmoiの後処理、systemd unit、Claude Codeのhook定義など他のプログラムから起動され、
  `pytools`パッケージの外でエンドユーザー環境（LinuxとWindows）で動く実行ファイルの置き場
  - chezmoiで配布せず、`~/dotfiles`の作業ツリーから直接実行する。両OSで動く書き方とする
  - 例: `libexec/update_dotfiles.py`・`libexec/claude-hook-pretooluse.ps1`・`libexec/keep-awake.ps1`
- `bin/`: ユーザーのPATHに追加して使うコマンド。リポジトリ直下でgit管理し、
  `~/dotfiles/bin`（Linux）/`%USERPROFILE%\dotfiles\bin`（Windows）にPATHを通す
  - 両OS対応のコマンドはLinux版とWindows版（`.cmd`／`.ps1`）を併置する
  - 例: `bin/update-dotfiles`↔`bin/update-dotfiles.cmd`

判断に迷ったら「エンドユーザー環境で実行されるか」を基準に決める。開発とCIの工程からしか動かないなら`scripts/`、
エンドユーザー環境で他のプログラムから起動されるなら`libexec/`、PATHから直接起動するなら`bin/`が適切。

### `bin/`を変えるときの確認

単純なコマンドラッパーのペアは`scripts/new_bin_cmd.py <name> <command...>`で生成できる。
`bin/<name>`と`bin/<name>.cmd`を生成する。

`bin/`直下のスクリプトを追加・移設・削除する際は、以下を同時に見直す。

- Linuxの配布設定: `.bashrc`のPATH追加行
- Windowsの配布設定: `pytools/_internal/setup_bin_path.py`によるユーザーPATH追記
- `.github/workflows/ci.yaml`の「主要ファイルの存在確認」ステップ

新しいOS別`run_*`スクリプトを追加する場合は`.chezmoiignore`にも除外エントリを追加。

## bash補完（`completions/`）

対象はLinux/bashのみ。Windowsではbash補完を提供しない。

補完スクリプトはログイン時の`register-python-argcomplete`実行コストを避けるため事前生成してリポジトリにチェックインする。
`completions/*.bash`を`.bashrc`がすべて`source`する。コマンド追加時に`.bashrc`を編集する必要はない。
`scripts/gen_completions.py`は生成先を2箇所へ分岐して書き込む。
通常はpyfltrのcustom formatterから統合生成ランナー経由で実行する。
`pyproject.toml`の`[project.scripts]`由来のコマンドは`completions/_pytools.bash`へ書き込む。
`agent-toolkit/scripts/*.py`のうちargcompleteマーカーを持つスクリプトが対象で、
対応するbashラッパーが`agent-toolkit/bin/`配下に存在するコマンド（`atk`等）に限る。
これらの補完は`scripts/gen_completions.py`が`agent-toolkit/completions/atk.bash`へ書き込む。

新しいCLIに補完を追加する場合は、CLIモジュールへのマーカー配置と`enable_completion()`呼び出しをコード側コメントに従い追加し、
補完スクリプトを再生成する。

### 補完スクリプトの再生成・検証

```bash
uv run --frozen python scripts/sync_generated_files.py  # 全生成物を冪等同期
uv run --frozen pyfltr fast                             # 高速ツールと生成物を同期
```

手書き補完が必要な場合（`bin/`配下コマンドのうち`gen_completions.py`の収集対象外のものなど）は
`completions/<name>.bash`を新規追加する。`_`プレフィックスのファイルは自動生成物の慣習として予約する。
`agent-toolkit/completions/atk.bash`は`gen_completions.py`の自動生成対象のため、この手順は当てはまらない。

## Windows PowerShellスクリプトの注意事項

- `.ps1.tmpl`は`.gitattributes`で`eol=crlf`を指定している（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述

## agent-toolkitの3形式配布

`agent-toolkit/`はAgent Plugins、Claude Code、Codexが共有するプラグインルートである。
`skills/`の実体を3形式で共有し、形式ごとのmanifestとMCP設定だけを分ける。

| 対象 | 役割と生成方法 |
| --- | --- |
| `.claude-plugin/plugin.json`・`.mcp.json` | Claude Code向け設定であり、metadataとClaude専用を含むMCP server定義の大元 |
| `plugin.json`・`.mcp.codex.json`・`mcp.json` | Agent Plugins v1向け生成物。大元の設定から共有許可済みserverだけを固定schemaへ写像する |
| `.codex-plugin/plugin.json`・`hooks/hooks.codex.json` | Codex向け生成物。大元の設定から許可済みの要素だけを写像する |
| `rules/`・`agents/`・`hooks/`・`bin/`・`scripts/`・`share/` | Claude Code・Codex・配布処理が使う固有資源。Agent Pluginsの可搬要素としては扱わない |

`pytools/_internal/codex_plugin_manifests.py`がAgent PluginsとCodexの生成物の生成と差の確認を担い、生成器の起動スクリプト`scripts/sync_codex_plugin_manifests.py`とpost-applyがこれを使う。
Codex向け`agents_server`はplugin rootを作業ディレクトリに固定した`uv run --project . --locked --no-default-groups agent_toolkit/agents_server_mcp.py`として生成する。Claude Code向けの`${CLAUDE_PLUGIN_ROOT}`展開はCodexの起動契約へ流用しない。
`scripts/sync_generated_files.py`は同生成器を統合実行し、生成物を冪等に更新する。

dotfilesはClaude Code・Agent Plugins向けの`agent-toolkit/`を元にし、Codex向けには`agent-toolkit-codex/`を生成する。
`agent-toolkit-codex/`はAgent Plugins用の直下`plugin.json`と`mcp.json`を除き、`.codex-plugin/plugin.json`、hook、skill、Python実装、lockfileその他の実行資源を通常ファイルとして含む。
Codex 0.154.0はプラグイン導入時にsourceをsnapshotするため、`agent-toolkit-codex/`は相対シンボリックリンクを含めない。
`.agents/plugins/marketplace.json`だけが`./agent-toolkit-codex`を参照し、Claude CodeとAgent Pluginsは引き続き`agent-toolkit/`を参照する。
`agent-toolkit-codex/`はGitで追跡せず、`update-dotfiles`のpost-applyがCodex plugin導入の直前に生成する。
手動で再生成する場合は`scripts/sync_codex_plugin_manifests.py`を実行し、`--check`で大元の定義との一致を確認する。
生成に失敗した場合はpost-applyが非0で終了し、失敗したstep名と詳細を更新logへ記録する。

Codex hookはPATH上の`~/.local/bin/atk-hook`（Windowsでは`atk-hook.cmd`）から起動する。
このコマンドは`codex plugin list --json`に示された有効な現行版を毎回解決し、イベント名、標準入出力および終了状態をhook本体へ渡す。
インストーラーは`codex plugin add`より先にこのコマンドを配置し、導入後に現行版のhook実体を確認する。
初回切替時に限り、更新前の版付きhookコマンドを保持したセッションのために旧キャッシュを一時退避し、CLIが削除した場合は復元する。以降の更新に旧版保存台帳は設けない。
プラグインの通常のversion別cache管理はCodex公式CLIへ委ねる。

agent-toolkitには、公開互換インストーラーである`install-claude.sh`・`install-claude.ps1`を使う単体導入と、
chezmoiの`post_apply`を使うdotfiles導入がある。既存の外部参照を維持するため、インストーラーと
`docs/guide/claude-code-guide.md`の名前はClaude Code・Codex統合後も変更しない。

| 導入方法 | マーケットプレイス | 設定対象 |
| --- | --- | --- |
| 単体インストーラー | Gitマーケットプレイス`ak110/dotfiles` | Claude Codeルール、双方のプラグイン、共有`agents_server` MCP、`atk` |
| dotfiles `post_apply` | ローカル生成物 | 単体導入の対象に加え、Codex向け`AGENTS.md`と共有リンク |

- agent-toolkitのCodex向けskillsはplugin marketplace経由で配布する。Agent Plugins・Codex向けmanifestは
  Claude Code向けmanifestを元にして`scripts/sync_generated_files.py`で生成する
- `setup_codex_links.py`はdotfiles固有スキルと`docs`だけをリンクする。`agent-toolkit/rules/`は`sync_agent_toolkit_rules.py`が配布先へ同期する
- Codex hookはイベント名、matcher、入力契約を確認した許可表へ登録したものだけを派生manifestへ含める

### `post_apply.py`の工程の依存

- `post_apply.py`は互いに依存しない工程を同時に実行し、工程間の順序を`_StepSpec`の先行工程の宣言で保つ。リンク同期、Claude Code plugin、Codex plugin、旧User scope MCPの移行の順序もこの宣言で保つ。先行工程を宣言する対象は、同じ資源を扱う工程の組と、先行工程が導入する実行ファイルを使う工程とする。
  資源は設定ファイルの読み書き、プロセスとユーザーのPATH、npmとmiseの管理領域、plugin cache、Claude Code pluginの複製元である`agent-toolkit/`（`.venv`を含む）、systemd、codexプロセスの稼働判定を指す。
  子プロセスやサービスの再起動を経由して間接的に書き換える資源も含める。例えば`atk serve`工程が再起動したサービスは`uv run --project <dotfiles>/agent-toolkit`で`.venv`を再同期する。工程を追加する場合も同じ基準で宣言する
- `post_apply.py`の工程のうち、HOMEで解決されない実機の共有資源（systemdのユーザーマネージャー、`/dev/shm`）を操作する工程には`_StepSpec`の`host_resources`を付ける。
  HOMEが実行ユーザーのパスワードデータベース上のホームと異なる実行（手動観測やテスト）では、この工程を実行せず成功かつ変更なしとし、理由を画面へ出力する。
  `systemctl --user`はHOMEではなく`XDG_RUNTIME_DIR`とD-Busで実機のユーザーマネージャーへ接続するため、HOMEの差し替えでは隔離できない。
  工程を追加する場合も同じ基準で付ける
- `post_apply.py`の画面には工程ごとの出力を列挙順にまとめて表示し、開始行、ロガー`httpx`のINFO、`claude` CLIの実行記録、実行中のOSを対象外とする工程の行は永続ログ（`update-dotfiles.log`）にだけ残す

### agents_server MCPの配置と寿命

共有MCP設定の大元が`agents_server`を定義し、`${CLAUDE_PLUGIN_ROOT}/agent_toolkit/agents_server_mcp.py`を
plugin rootを`uv run --project`へ指定し、lockfileを固定して起動する。生成器は共有許可リストのMCPをAgent PluginsとCodexのmanifestへ射影し、
Codex側では`${PLUGIN_ROOT}`へ変換する。MCPサーバーは`start`が解決した候補のengineに従ってCodex backendまたはClaude backendを選択する。

公開ツール（`start`、`send_message`、`kill`、`list`、`show`、`stop`）と`atk agents wait`の入力、応答、待機上限および保持期限の契約は[design-agents-server.md](design-agents-server.md)の「公開ツールの契約」が記録する。
MCP終了時は自身が起動した子プロセスをPID指定で終了し、共有daemonや永続registryを持たない。

MCP moduleの初期化時にCodex backendとClaude backendのローカルmoduleを読み込む。
プラグイン配置の寿命に依存するローカルmoduleの遅延importは行わず、共有状態型はbackendとMCP層の共通moduleへ分離する。
Claude Agent SDKはCodex実行時の依存と起動コストを増やさないため、Claude engineでoptionsを構築する時点まで遅延する。
プラグイン導入後のウォームアップは、同じplugin root指定の起動形へ`--check-dependencies`を渡し、
PEP 723の依存importとClaudeAgentOptionsの構築だけを確認する。外部Claude/Codex sessionは開始しない。

## ホーム配下のファイルを編集する前の確認

`~/.config/`・`~/.claude/`などホーム直下のファイルを編集する場合、
まず`chezmoi managed | grep <相対パス>`で配布対象かを確認。
配布対象であれば`.chezmoi-source/`側を編集（直接編集は次回`chezmoi apply`で上書きされる）。
設定の出所調査には`git config --show-origin --get <key>`も有効。
