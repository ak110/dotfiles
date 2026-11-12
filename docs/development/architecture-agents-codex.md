# アーキテクチャ: agents_serverとCodexへの配布

## agent-toolkitのホスト別配布

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
PEP 723の依存importとClaudeAgentOptionsの構築だけを確認する。
外部Claude/Codex sessionは開始しない。

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
- `~/.codex/skills/operate-ak110-projects`と`~/.codex/docs`: dotfiles固有スキルと`.chezmoi-source/dot_claude/docs`へのリンク

`agent-toolkit/share/rules/common`は配布先で境界を付けた本文へ書き換えるためコピーで同期し、書き換えを要さない共有対象はリンクで配布する。
chezmoiの`symlink_`はWindowsで特権不足により失敗するため、`chezmoi apply`後処理（`pytools.post_apply`）の専用ステップがリンクを生成する（Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション）。
`~/.codex/rules`はCodexの承認ルール用ディレクトリでありMarkdownルールと互換性がないため、Markdownルールは`~/.codex/agent-toolkit/rules`へ置く。

Codexはプロジェクト直下の`.claude/rules/`と`.claude/skills/`を自動では読まない。
プロジェクト専用スキルは`.agents/skills -> .claude/skills`のシンボリックリンクで共有する。
Codexは`~/.codex/AGENTS.md`の読込指示に従い、作業の開始時に次の規範を読む。

- プロジェクト直下の`.claude/rules/`
- `~/.claude/CLAUDE.md`と`~/.claude/rules/`直下の`*.local.md`
- 作業ディレクトリからルートまでの各ディレクトリの`CLAUDE.local.md`と、同階層に`AGENTS.md`が無い場合の`CLAUDE.md`

会話圧縮の後も対象の本文を再取得し、起動・再開指示に同じ本文が境界付きで含まれる場合はその本文を使う。

プロジェクト規範は`AGENTS.md`を実体ファイルとし、`CLAUDE.md`は置かない（コピー欠落やシンボリックリンク非対応環境での障害を避けるため）。
Claude Codeは`CLAUDE.md`・`.claude/CLAUDE.md`・`CLAUDE.local.md`のいずれかがあると`AGENTS.md`を読まない。
このため個人用の`CLAUDE.local.md`を置いたプロジェクトでは、`atk setup-project`が`@AGENTS.md`を取り込むだけの`CLAUDE.md`アダプターを置き、`.git/info/exclude`で追跡対象から外す。
