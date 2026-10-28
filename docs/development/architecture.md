# アーキテクチャ

本リポジトリはchezmoi管理のdotfilesリポジトリ。
主要なコンポーネントとその役割を以下に示す。

- `.chezmoiroot`でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離する
- `.chezmoi-source/`内がchezmoiのソースディレクトリ（`dot_`プレフィックス→`~/.*`にデプロイ）
- `.chezmoi-source/dot_claude/`: Claude Code用のユーザー設定。`~/.claude/`へデプロイする
- `.chezmoi-source/dot_codex/`: Codex用のユーザー設定。`~/.codex/`へデプロイする
- `.chezmoi-source/dot_gemini/`: Antigravity CLI用のユーザー設定（`GEMINI.md`と`antigravity-cli/skills/`）。`~/.gemini/`へデプロイする
- `agent-toolkit/`: Agent Plugins、Claude Code、Codexが共有するプラグインルートであり、OpenCode向けスキルと規範の原本（「agent-toolkitのホスト別配布」を参照）
- `agent-toolkit-codex/`: `agent-toolkit/`から生成するCodex向けプラグインルート。Gitで追跡せず、`update-dotfiles`のpost-applyが生成する
- `plugins/`: dotfilesユーザーが置く拡張ディレクトリ（Git追跡対象外）。直下の各プラグインの`.chezmoi-source/`と`post_apply.py`を本体反映後に名前順で適用する
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
対応するbashラッパーが`agent-toolkit/scripts/`配下に存在するコマンド（`atk`等）に限る。
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

## agent-toolkitのホスト別配布

`agent-toolkit/`はAgent Plugins、Claude Code、Codexが共有するプラグインルートである。
`skills/`の原本を共有し、ホストごとのmanifest、hook、MCP設定と常時規範の配置を生成する。
OpenCodeの対話ホストには、同じ原本からスキルと常時規範を導入する。

| 対象 | 役割と生成方法 |
| --- | --- |
| `.claude-plugin/plugin.json`・`.mcp.json` | Claude Code向け設定であり、metadataとClaude専用を含むMCP server定義の大元 |
| `plugin.json`・`.mcp.codex.json`・`mcp.json` | Agent Plugins v1向け生成物。大元の設定から共有許可済みserverだけを固定schemaへ写像する |
| `.codex-plugin/plugin.json`・`hooks/hooks.codex.json` | Codex向け生成物。大元の設定から許可済みの要素だけを写像する |
| `rules/`・`agents/`・`hooks/`・`scripts/`・`share/` | Claude Code・Codex・配布処理が使う固有資源。Agent Pluginsの可搬要素としては扱わない |
| OpenCodeの`skills/`と`agent-toolkit.md` | post-applyが共有スキルと常時規範をグローバル検出先へ同期する |

新規engineの追加時にホスト導入の要否と理由を判断・記録する手順は、`.claude/skills/agent-toolkit-edit/SKILL.md`が定める。

`pytools/_internal/codex_plugin_manifests.py`がAgent PluginsとCodexの生成物の生成と差の確認を担い、生成器の起動スクリプト`scripts/sync_codex_plugin_manifests.py`とpost-applyがこれを使う。
Codex向け`agents_server`はplugin rootを作業ディレクトリに固定する。
起動定義は`uv run --no-config --no-project --python 3.15 python scripts/launch.py agent_toolkit/agents_server_mcp.py`として生成する。
Claude Code向けの`${CLAUDE_PLUGIN_ROOT}`展開はCodexの起動契約へ流用しない。
`scripts/sync_generated_files.py`は同生成器を統合実行し、生成物を冪等に更新する。

dotfilesはClaude Code・Agent Plugins向けの`agent-toolkit/`を元にし、Codex向けには`agent-toolkit-codex/`を生成する。
`agent-toolkit-codex/`はAgent Plugins用の直下`plugin.json`と`mcp.json`を除き、`.codex-plugin/plugin.json`、hook、skill、Python実装、lockfileその他の実行資源を通常ファイルとして含む。
Codex 0.154.0はプラグイン導入時にsourceをsnapshotするため、`agent-toolkit-codex/`は相対シンボリックリンクを含めない。
`.agents/plugins/marketplace.json`だけが`./agent-toolkit-codex`を参照し、Claude CodeとAgent Pluginsは引き続き`agent-toolkit/`を参照する。
`agent-toolkit-codex/`はGitで追跡せず、`update-dotfiles`のpost-applyがCodex plugin導入の直前に生成する。
手動で再生成する場合は`scripts/sync_codex_plugin_manifests.py`を実行し、`--check`で大元の定義との一致を確認する。
生成に失敗した場合はpost-applyが非0で終了し、失敗したstep名と詳細を更新logへ記録する。

共有するCodex hook定義は、ホストが渡す`${PLUGIN_ROOT}/scripts/launch.py`からその版のhookを起動する。
dotfilesが生成する`agent-toolkit-codex/`のhook定義は、個人binの`atk-hook`を安定した起動コマンドにする。
インストーラーは`codex plugin add`より先にこのコマンドを配置する。
`atk-hook`はlive sessionが保持する`PLUGIN_ROOT`を優先し、その変数が指す版付きCodex cacheが欠落した場合は、Claude Code側に残る同版cacheを同じCodex絶対パスへ原子的に復旧する。
同版が無い場合はClaude Codeの現行導入版からhookを起動し、編集途中のdotfiles作業ツリーは自動fallbackに使わない。
更新前の全通常versionディレクトリを複製し、導入後の状態確認とdaemon処理を含む更新関数内のCodex CLI呼出が全て終了した後に、CLIが削除した旧versionを同じ絶対パスへ復元する。
現行versionの導入と有効状態の管理はCodex公式CLIへ委ね、旧版保存台帳は設けない。

agent-toolkitには、公開互換インストーラーである`install-claude.sh`・`install-claude.ps1`を使う単体導入と、
chezmoiの`post_apply`を使うdotfiles導入がある。既存の外部参照を維持するため、インストーラーと
`docs/guide/claude-code-guide.md`の名前はClaude Code・Codex統合後も変更しない。

| 導入方法 | マーケットプレイス | 設定対象 |
| --- | --- | --- |
| 単体インストーラー | Gitマーケットプレイス`ak110/dotfiles` | Claude Codeルール、双方のプラグイン、共有`agents_server` MCP、`atk` |
| dotfiles `post_apply` | ローカル生成物 | 単体導入の対象に加え、Codex向け`AGENTS.md`と共有リンク、OpenCode向けスキルと常時規範 |

- agent-toolkitのCodex向けskillsはplugin marketplace経由で配布する。Agent Plugins・Codex向けmanifestは
  Claude Code向けmanifestを元にして`scripts/sync_generated_files.py`で生成する
- `setup_codex_links.py`はdotfiles固有スキルと`docs`だけをリンクする。`agent-toolkit/rules/`は`sync_agent_toolkit_rules.py`が配布先へ同期する
- Codex hookはイベント名、matcher、入力契約を確認した許可表へ登録したものだけを派生manifestへ含める

### OpenCodeの対話ホストへの導入

`pytools/_internal/install_opencode_toolkit.py`は、OpenCode CLI導入後のfullモードの後処理として動く。
Linux・Windowsとも`XDG_CONFIG_HOME`、未設定なら`~/.config`配下の`opencode/`を使い、CLI不在時は案内してスキップする。
`OPENCODE_CONFIG_DIR`が指定されている場合は、その設定ディレクトリを優先する。
`skills/agent-toolkit-<名前>/`と`skills/agent-basics-<名前>/`へスキル本体と付属資源をコピーする。
同名スキルの衝突を避ける名前空間を付け、frontmatterの`name`をディレクトリ名へ合わせる。
本文と付属資源は原本を保持し、手動起動専用スキルには`metadata["opencode/autoinvoke"]: "false"`を加える。
独自スキルは保持し、専用の所有markerがある配布先だけで更新・廃止した資源を同期する。

常時規範は`agent-toolkit.md`へ`atk-auto`境界とともに集約し、元の規範本文、plugin rootとスキル名の対応を保持する。
スキル本文のroot変数と相対参照は、原本のplugin rootとスキルディレクトリを起点に解決する。
`opencode.jsonc`、既存設定が`opencode.json`だけならそのファイルの`instructions`へ、専用規範の絶対パスを1回だけ追加する。
グローバル`AGENTS.md`とClaude互換規範の読込順位、既存のprovider・permission・mcpなどの値を保持する。
この導入はMCP登録、TS hook移植と単体インストーラーの対象追加を含まない。
ホストが定める検出と読込の契約は[skills](https://opencode.ai/docs/skills/)と[rules](https://opencode.ai/docs/rules/)にある。

### `post_apply.py`の工程間の順序

- dotfilesプラグインの適用は`pytools/_internal/apply_dotfiles_plugins.py`が担い、本体の後処理が終わってから実行する。プラグインごとのchezmoi永続状態を本体から分離し、本体と配布先が重なる場合は警告してプラグインの内容を上書きする。配置と失敗時の扱いは[operations.md](operations.md)の「dotfilesプラグインの適用と更新」を参照
- `post_apply.py`は互いの成果を使わない工程を同時に実行し、工程間の順序を`_StepSpec`の先行工程の宣言で保つ。リンク同期、Claude Code plugin、Codex plugin、旧User scope MCPの移行の順序もこの宣言で保つ。先行工程を宣言する対象は、同じ資源を扱う工程の組と、先行工程が導入する実行ファイルを使う工程とする。
  資源は設定ファイルの読み書き、プロセスとユーザーのPATH、npmとmiseの管理領域、plugin cache、Claude Code pluginの複製元である`agent-toolkit/`、依存環境を保存するデータ領域、systemd、codexプロセスの稼働判定を指す。
  子プロセスやサービスの再起動を経由して間接的に書き換える資源も含める。例えば`atk serve`工程が再起動したサービスは`scripts/launch.py`を経てデータ領域の依存環境を同期する。工程を追加する場合も同じ基準で宣言する
- `post_apply.py`の工程のうち、HOMEで解決されない実機の共有資源（systemdのユーザーマネージャー、`/dev/shm`）を操作する工程には`_StepSpec`の`host_resources`を付ける。
  HOMEが実行ユーザーのパスワードデータベース上のホームと異なる実行（手動観測やテスト）では、この工程を実行せず成功かつ変更なしとし、理由を画面へ出力する。
  `systemctl --user`は`XDG_RUNTIME_DIR`とD-Busで実機のユーザーマネージャーへ接続するため、HOMEの差し替えでは隔離できない。
  工程を追加する場合も同じ基準で付ける
- `post_apply.py`は工程ごとの出力を列挙順にまとめて画面へ表示する
- 進捗の表示は、標準エラーが端末でエージェント環境でない場合（`is_human_terminal()`が真）に限り、標準エラーの1行を書き換える形で出す。パイプへの出力とエージェント環境では進捗を出さない
- 工程の例外は「工程名: 失敗: 例外の型: メッセージ」の1行だけを画面へ出力し、Tracebackは永続ログに残す。工程が`PostApplyOutcome`の`next_action`を返した失敗は、その工程の行に工程固有の次の操作を出す
- 実行全体の失敗後の次の操作と永続ログの所在は1回だけ出力する。環境変数`UPDATE_DOTFILES_RUN_ID`がある`update-dotfiles`経由の実行では、`update-dotfiles`の終了時に出す。この環境変数が無い`dotfiles-post-apply`の単独実行では、post-applyの集計の行（`完了: 更新 N 件 / スキップ N 件 / 失敗 N 件`）の後に出す
- 開始行、ロガー`httpx`のINFO、`claude` CLIの実行記録、実行中のOSを対象外とする工程の行は永続ログ（`update-dotfiles.log`）にだけ残す

### Python依存環境の共有

`plugin_environment.py`は選択したPythonの実行ファイルパス、OS・アーキテクチャ、依存導入に作用するpyproject設定と`uv.lock`を正規化して共有環境を識別する。
配布root、プラグイン版、文書・コード・lint設定の差は環境を分ける条件に含めない。
依存だけを同期し、起動ごとのrootを`PYTHONPATH`の先頭へ置いて、その配布版のコードと資源を参照する。
pyfltr MCPも`_common/pyfltr_entry.py`からlockfileで準備したconsole entry pointへ接続する。
この境界でplugin専用のuv環境指定を解除し、pyfltrがチェック対象のリポジトリの依存環境を使えるようにする。

準備には`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`を実行する。

`--prepare`の後ろのtargetは配布root内に存在する起動対象を確認する必須引数であり、共有環境の選択には影響しない。上記の組み合わせが未準備の場合に実行し、同じ組み合わせなら別の配布rootやworktreeでも準備済み環境を共有する。別worktreeを指定する場合の`<plugin root>`は、そのworktree内の`agent-toolkit`の絶対パスに置き換える。rootは`launch.py`の配置または`--project`から解決され、cwdの変更だけでは切り替わらない。

`update-dotfiles`の後処理は両ホストの実際の有効版と作業ツリー版を列挙し、公開インストーラーも同じ準備処理を使う。
通常のCLI・hook・MCPは準備済み環境のPythonを直接起動するため、起動のたびに依存を照会しない。
未準備時の診断は`update-dotfiles`または公開インストーラーでの再準備を案内する。
更新後は新規起動が新しい依存キーへ切り替わり、稼働中の旧版が使う既存環境を保持する。
共有化の更新には旧環境の自動回収を加えない。
minimalの明示的な`--prune`は使用中保護の判定後にAI用環境を削除する。

### agents_server MCPの配置と寿命

共有MCP設定の大元が`agents_server`を定義し、`${CLAUDE_PLUGIN_ROOT}/scripts/launch.py`から`agent_toolkit/agents_server_mcp.py`を起動する。
launch.pyは準備済みの共有依存環境を配布root外のデータ領域から選び、その起動が指定したplugin rootのコードを実行する。
生成器は共有許可リストのMCPをAgent PluginsとCodexのmanifestへ射影し、Codex側ではplugin root内の作業ディレクトリからlaunch.pyを起動する。
MCPサーバーは`start`が解決した候補のengineに従ってCodex backendまたはClaude backendを選択する。

版付き配布物から起動したサーバーは、同じ配布元・版に対応するmanaged-tempの保持実体を複数プロセスで共有する。
起動側と回収側は`_common/plugin_root_leases.py`の共通契約で使用中の実体を保護する。
文書解決と内側MCPの起動も同じ保持実体を使い、配布元の更新・削除後も起動時の版へ到達できる。

公開ツール（`start`、`send_message`、`kill`、`list`、`show`、`stop`）と`atk agents wait`の入力、応答、待機上限および保持期限の契約は[design-agents-server.md](design-agents-server.md)の「公開ツールの契約」が記録する。
MCP終了時は自身が起動した子プロセスをPID指定で終了し、共有daemonや永続registryを持たない。

MCP moduleの初期化時にCodex backendとClaude backendのローカルmoduleを読み込む。
プラグイン配置の存続を前提とするローカルmoduleの遅延importは行わず、共有状態型はbackendとMCP層の共通moduleへ分離する。
Claude Agent SDKはCodex実行時の依存と起動コストを増やさないため、Claude engineでoptionsを構築する時点まで遅延する。
プラグイン導入後のウォームアップは、導入済み版のlaunch.pyへ`--check-dependencies`を渡し、
PEP 723の依存importとClaudeAgentOptionsの構築だけを確認する。外部Claude/Codex sessionは開始しない。

### `atk`の解決とセッションへの受け渡し

`atk`の実体は`agent-toolkit/scripts/atk`であり、マーケットプレイス経由の導入では`~/.claude/plugins/cache/<marketplace>/agent-toolkit/<version>/scripts/atk`に置かれる。
版ディレクトリは更新ごとに変わるため、インストーラーは`~/.local/bin/atk`（Windowsでは`atk.cmd`）へ最新版を動的に解決するラッパーを置く。
dotfiles導入では`chezmoi apply`が`~/dotfiles/agent-toolkit/scripts`をPATHへ加える。

Claude CodeのSessionStartは、`CLAUDE_ENV_FILE`が提供される場合にCLIのPATHとデータ領域を後続のBashへ渡し、別hookの設定と既存PATHを保持する。
このファイルが提供されない場合も、開始時のコンテキストにCLIの絶対パスを表示する。
MCPと`agents_server`の委譲先には、起動設定で同じ配布物のCLIとデータ領域を渡す。

### Codex向けの配布構成

dotfiles導入はCodex向けに次を配置する。
単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、これらを配置しない。

- `~/.codex/AGENTS.md`: Codex向けの基本記述と、agent-toolkitの基本原則・製品横断の実行運用を埋め込む（`scripts/sync_codex_agents.py`が生成）
- `~/.codex/agent-toolkit/rules`: agent-toolkitのルール原本を`atk-auto`の境界付き本文としてコピーで同期する
- `~/.codex/skills/ak110-projects-operations`と`~/.codex/docs`: dotfiles固有スキルと`.chezmoi-source/dot_claude/docs`へのリンク

`agent-toolkit/rules`は配布先で境界を付けた本文へ書き換えるためコピーで同期し、書き換えを要さない共有対象はリンクで配布する。
chezmoiの`symlink_`はWindowsで特権不足により失敗するため、`chezmoi apply`後処理（`pytools.post_apply`）の専用ステップがリンクを生成する（Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション）。
`~/.codex/rules`はCodexの承認ルール用ディレクトリでありMarkdownルールと互換性がないため、Markdownルールは`~/.codex/agent-toolkit/rules`へ置く。

Codexはプロジェクト直下の`.claude/rules/`と`.claude/skills/`を自動では読まない。
プロジェクト専用スキルは`.agents/skills -> .claude/skills`のシンボリックリンクで共有する。
`.claude/rules/`、`~/.claude/CLAUDE.md`、`~/.claude/rules/`直下の`*.local.md`、プロジェクト直下の`CLAUDE.local.md`は、`~/.codex/AGENTS.md`の読込指示に従ってCodexが作業の開始時に読む。

プロジェクト規範は`AGENTS.md`を実体ファイルとし、`CLAUDE.md`は置かない（コピー欠落やシンボリックリンク非対応環境での障害を避けるため）。
Claude Codeは`CLAUDE.md`・`.claude/CLAUDE.md`・`CLAUDE.local.md`のいずれかがあると`AGENTS.md`を読まない。
このため個人用の`CLAUDE.local.md`を置いたプロジェクトでは、`atk setup-project`が`@AGENTS.md`を取り込むだけの`CLAUDE.md`アダプターを置き、`.git/info/exclude`で追跡対象から外す。

## ホーム配下のファイルを編集する前の確認

`~/.config/`・`~/.claude/`などホーム直下のファイルを編集する場合、
まず`chezmoi managed | grep <相対パス>`で配布対象かを確認。
配布対象であれば`.chezmoi-source/`側を編集（直接編集は次回`chezmoi apply`で上書きされる）。
設定の出所調査には`git config --show-origin --get <key>`も有効。

### Antigravity向け配布

`pytools/_internal/antigravity_plugin.py`が`agent-toolkit-agy/`と`agent-basics-agy/`を生成し、
`scripts/sync_antigravity_plugin.py`とpost-applyが同じ同期処理を使う。
toolkit側には`plugin.json`、`skills/`、`agents/`、`rules/AGENTS.md`と追加の常時規範ファイル、
直下の`hooks.json`・`mcp_config.json`、Python実装とlockfileを含む。basics側は独立した品質スキル集で、
実行基盤のhook・MCP・Python環境を含めずに利用できる。
各manifestの名前は元の`agent-toolkit`・`agent-basics`を保持し、品質スキルの所有と名前空間を保つ。
生成rootはGit追跡対象外で、`scripts/sync_generated_files.py`の最後に同期する。
無指定は両方を同期し、`scripts/sync_antigravity_plugin.py --plugin agent-basics`はbasicsだけを同期する。
生成した`agent-basics-agy/`を選べば、toolkitの有効化を伴わずに品質基準を利用できる。
Claude Code・Codex・Agent Plugins向けの既存3形式と原本の品質本文は、この生成処理で変更しない。

共通常時規範は行境界で分割し、各ファイルを24,000バイト以内に保つ。
生成器は合計20,000トークンの予算をUTF-8の2 bytes/token換算でチェックし、
超過時は配布前に失敗させる。追加の分割ファイルは`trigger: always_on`でロードする。
プロジェクト独自の規範もホストの同じ予算を使うため、その総量は対象プロジェクトで確認する。

6イベントのhookは同じ生成rootの`scripts/atk-hook antigravity <イベント名>`を呼ぶ。
MCPは原本から`agents_server`・`pyfltr`を選び、root参照を絶対パスへ投影する。
スキル本文中の実行用rootも同じ絶対パスへ投影し、環境変数展開への依存を解消する。

post-applyはAntigravity CLI導入、Codex snapshotの生成とGemini設定反映の後に配布rootを同期する。
続いて`~/.gemini/config/plugins.json`の`entries`へ
作業ツリーの絶対パスと`include_only: ["agent-toolkit-agy", "agent-basics-agy"]`を追加する。
旧版が作成したtoolkitだけの管理対象entryは、同じentryを両方の指定へ更新する。
他の登録と継承設定を保持し、同じ登録は再追加しない。
プラグインの有効/無効を記録するユーザーの`config.json`は変更しない。
登録や更新後は新しいagyセッションで反映を確認する。

Antigravityのスキル本文にも24,000バイトの読込上限があるため、両プラグインで共通の生成処理が上限を超える
`SKILL.md`のfrontmatterを短いSKILL.mdに保持し、本文を
`references/antigravity-full-instructions.md`へ保存する。短いSKILL.mdは起動時に全文資料を読む。
全文資料の相対参照は元のスキルディレクトリを起点に解決し、原本の必要本文と参照先を保持する。
`share/*.subagent.md`は入力契約を保持した原本として配送し、`agents/`に
対応する名前と原本の読込指示を持つnative agent定義を生成する。
