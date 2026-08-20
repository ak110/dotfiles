# アーキテクチャ

本リポジトリはchezmoi管理のdotfilesリポジトリ。
主要なコンポーネントとその役割を以下に示す。

- `.chezmoiroot`でソースステート（`.chezmoi-source/`）とプロジェクトインフラを分離する
- `.chezmoi-source/`内がchezmoiのソースディレクトリ（`dot_`プレフィックス→`~/.*`にデプロイ）
- `.chezmoi-source/dot_claude/`: Claude Code用のユーザー設定。`~/.claude/`へデプロイする
- `.chezmoi-source/dot_codex/`: Codex用のユーザー設定。`~/.codex/`へデプロイする
- `pytools/`: Pythonコマンドラインツール群（`uv tool install`でインストール）
- `rust/`: Rust製コマンドラインツール群（CIでビルドしGitHub Releaseへ配布）
- `scripts/`: リポジトリ内部から呼ばれるスクリプト置き場（prek・Makefile・Claude Codeフック等。配布対象外）
- テンプレートからリポジトリルートのファイルを参照する場合は`{{ .chezmoi.workingTree }}`を使用
  - 例: `{{ include (joinPath .chezmoi.workingTree "pyproject.toml") }}`

## 開発者と利用者の対象環境

本dotfilesは以下の二者を想定している。配布対象と開発対象でサポート範囲が異なるため、ファイル追加時にどちら用かを確認。

- 利用者: Linux+Windows（配布対象。`install.sh`/`install.ps1`/`install-claude.sh`/`install-claude.ps1`/
  chezmoi管理ファイルはすべて両OS対応とする）
- 開発者: Linuxのみ（`make test`/prek/CIの開発系ジョブはLinux前提。macOS/Windowsでのローカル開発は非対応で構わない）

この区別に基づき、スクリプトの配置先を以下のように分ける。

- `scripts/`: prek・Makefile・Claude Codeフックなどリポジトリ内部から呼ばれるスクリプト置き場
  - chezmoiで配布しない。Linux前提で書いてよい
  - 例: `scripts/check-templates.sh`・`scripts/check-cmd-encoding.sh`・
    `scripts/check-ps1-bom.sh`・`scripts/run-psscriptanalyzer.sh`・`scripts/claude_hook_pretooluse.py`
- `bin/`: ユーザーのPATHに追加して使うコマンド。リポジトリ直下でgit管理し、
  `~/dotfiles/bin`（Linux）/`%USERPROFILE%\dotfiles\bin`（Windows）にPATHを通す
  - 両OS対応のコマンドはLinux版とWindows版（`.cmd`／`.ps1`）を併置する
  - 例: `bin/update-dotfiles`↔`bin/update-dotfiles.cmd`

判断に迷ったら「他者の環境で直接実行されるか」で切り分ける。prek経由でしか動かないなら`scripts/`が適切。

単純なコマンドラッパーのペアは`scripts/new-bin-cmd.py <name> <command...>`で生成できる。
`bin/<name>`と`bin/<name>.cmd`を生成する。

`bin/`直下のスクリプトを追加・移設・削除する際は、以下を同時に見直す。

- Linuxの配布経路: `.bashrc`のPATH追加行
- Windowsの配布経路: `pytools/_internal/setup_bin_path.py`によるユーザーPATH追記
- `.github/workflows/ci.yaml`の「主要ファイルの存在確認」ステップ

新しいOS別`run_*`スクリプトを追加する場合は`.chezmoiignore`にも除外エントリを追加。

## bash補完（`completions/`）

対象はLinux/bashのみ。Windowsではbash補完を提供しない。

補完スクリプトはログイン時の`register-python-argcomplete`実行コストを避けるため事前生成してリポジトリにチェックインする。
`completions/*.bash`を`.bashrc`がすべて`source`する。コマンド追加時に`.bashrc`を編集する必要はない。
`scripts/gen-completions.py`は生成先を2箇所へ分岐して書き込む。
通常はpyfltrのcustom formatterから統合生成ランナー経由で実行する。
`pyproject.toml`の`[project.scripts]`由来のコマンドは`completions/_pytools.bash`へ書き込む。
`agent-toolkit/scripts/*.py`のうちargcompleteマーカーを持つスクリプトが対象で、
対応するbashラッパーが`agent-toolkit/bin/`配下に存在するコマンド（`atk`等）に限る。
これらの補完は`scripts/gen-completions.py`が`agent-toolkit/completions/atk.bash`へ書き込む。

新しいCLIに補完を追加する場合は、CLIモジュールへのマーカー配置と`enable_completion()`呼び出しをコード側コメントに従い追加し、
補完スクリプトを再生成する。

### 補完スクリプトの再生成・検証

```bash
uv run --frozen python scripts/sync_generated_files.py  # 全生成物を冪等同期
uvx pyfltr fast                                         # 高速ツールと生成物を同期
```

手書き補完が必要な場合（`bin/`配下コマンドのうち`gen-completions.py`の収集対象外のものなど）は
`completions/<name>.bash`を新規追加する。`_`プレフィックスのファイルは自動生成物の慣習として予約する。
`agent-toolkit/completions/atk.bash`は`gen-completions.py`の自動生成対象のため、この手順は当てはまらない。

## Windows PowerShellスクリプトの注意事項

- `.ps1.tmpl`は`.gitattributes`で`eol=crlf`を指定している（Windows PowerShell 5.1はLF改行だと構文解析に失敗する）
- 全スクリプト冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述

## agent-toolkitの3形式配布

`agent-toolkit/`はAgent Plugins、Claude Code、Codexが共有するプラグインルートである。
`skills/`の実体を3形式で共有し、形式ごとのmanifestとMCP設定だけを分ける。

| 対象 | 役割と生成経路 |
| --- | --- |
| `.claude-plugin/plugin.json`・`.mcp.json` | Claude Code向け設定であり、metadataとClaude専用を含むMCP server定義の正本 |
| `plugin.json`・`.mcp.codex.json`・`mcp.json` | Agent Plugins v1向け生成物。正本から共有許可済みserverだけを固定schemaへ写像する |
| `.codex-plugin/plugin.json`・`hooks/hooks.codex.json` | Codex向け生成物。正本から許可済みの要素だけを写像する |
| `rules/`・`agents/`・`hooks/`・`bin/`・`scripts/` | Claude Code・Codex・配布処理が使う固有資源。Agent Pluginsの可搬要素としては扱わない |

`scripts/sync_codex_plugin_manifests.py`がAgent PluginsとCodexの生成物を同期する。
`scripts/sync_generated_files.py`は同生成器を統合実行し、生成物を冪等に更新する。

agent-toolkitには、公開互換入口である`install-claude.sh`・`install-claude.ps1`を使う単体導入と、
chezmoiの`post_apply`を使うdotfiles導入がある。既存の外部参照を維持するため、インストーラーと
`docs/guide/claude-code-guide.md`の名前はClaude Code・Codex統合後も変更しない。

| 経路 | マーケットプレイス | 設定対象 |
| --- | --- | --- |
| 単体インストーラー | Gitマーケットプレイス`ak110/dotfiles` | Claude Codeルール、双方のプラグイン、Claude Code専用Codex App Server MCP、`atk` |
| dotfiles `post_apply` | ローカル生成物 | 単体経路の対象に加え、Codex向け`AGENTS.md`と共有リンク |

- agent-toolkitのCodex向けskillsはplugin marketplace経由で配布する。Agent Plugins・Codex向けmanifestは
  Claude Code向けmanifestを正本として`scripts/sync_generated_files.py`で生成する
- `setup_codex_links.py`はdotfiles固有スキルと、plugin非対応のagents・rulesだけをリンクする
- `post_apply.py`はリンク同期、Claude Code plugin、Codex plugin、旧User scope MCPの移行の順に処理する
- Codex hookはイベント名、matcher、入力契約を確認した許可表へ登録したものだけを派生manifestへ含める

### Codex App Server MCPの配置と寿命

Claude Code pluginの`.mcp.json`だけが`codex_app_server`を定義し、
`${CLAUDE_PLUGIN_ROOT}/scripts/codex_app_server_mcp.py`を`uv run --no-project --script`で起動する。
生成器はClaude用MCPから共有許可リストの`pyfltr`だけを`.mcp.codex.json`と`mcp.json`へ射影するため、
CodexまたはAgent PluginsへClaude専用MCPを再公開しない。

MCPサーバーは必要時に公式の`codex app-server --stdio`を子プロセスとして起動し、
`codex_start`、`codex_status`、`codex_wait`、`codex_result`、`codex_start_reply`を公開する。
`codex_start`は絶対`cwd`と固定の`approvalPolicy=never`・`dangerFullAccess`でthreadを開始し、
完了を待たず`session_id`を返す。`codex_result`で先行turnを回収してから同じsessionを継続する。
MCP終了時は自身が起動した子プロセスをPID指定で終了し、共有daemonや永続registryを持たない。

Claude Code pluginの`SessionStart`フックは、User scopeへ残る旧`codex mcp-server`定義を読み取り専用で診断する。
旧定義を検出した場合は`claude mcp get codex`による確認と、`claude mcp remove --scope user codex`による手動削除を案内する。
pluginの起動だけを契機に`~/.claude.json`を変更しない。

## ホーム配下のファイルを編集する前の確認

`~/.config/`・`~/.claude/`などホーム直下のファイルを編集する場合、
まず`chezmoi managed | grep <相対パス>`で配布対象かを確認。
配布対象であれば`.chezmoi-source/`側を編集（直接編集は次回`chezmoi apply`で上書きされる）。
設定の出所調査には`git config --show-origin --get <key>`も有効。
