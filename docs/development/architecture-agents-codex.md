# アーキテクチャ: agents_serverとCodexへの配布

## agents_server MCPの配置と寿命

共有MCP設定の大元が`agents_server`を定義し、`${CLAUDE_PLUGIN_ROOT}/scripts/launch.py`から`agent_toolkit/agents_server_mcp.py`を起動する。
launch.pyは準備済みの共有依存環境を配布root外のデータ領域から選び、その起動が指定したplugin rootのコードを実行する。

版付き配布物から起動したサーバーは、同じ配布元・版に対応するmanaged-tempの保持実体を複数プロセスで共有する。
起動側と回収側は`_common/plugin_root_leases.py`の共通契約で使用中の実体を保護する。
文書解決と内側MCPの起動も同じ保持実体を使い、配布元の更新・削除後も起動時の版へ到達できる。

公開ツール（`start`、`send_message`、`kill`、`list`、`show`、`stop`）と`atk agents wait`の入力、応答、待機上限および保持期限の契約は[design-agents-server-start.md](design-agents-server-start.md)などの公開ツール別の設計記録にある。

MCP moduleの初期化時にCodex backendとClaude backendのローカルmoduleを読み込む。
プラグイン配置の存続を前提とするローカルmoduleの遅延importは行わず、共有状態型はbackendとMCP層の共通moduleへ分離する。
Claude Agent SDKはCodex実行時の依存と起動コストを増やさないため、Claude engineでoptionsを構築する時点まで遅延する。
プラグイン導入後のウォームアップは、導入済み版のlaunch.pyへ`--check-dependencies`を渡し、PEP 723の依存importとClaudeAgentOptionsの構築だけを確認する。
外部Claude/Codex sessionは開始しない。

## `atk`の解決とセッションへの受け渡し

`atk`の実体は`agent-toolkit/scripts/atk`であり、マーケットプレイス経由の導入では`~/.claude/plugins/cache/<marketplace>/agent-toolkit/<version>/scripts/atk`に置かれる。
版ディレクトリは更新ごとに変わるため、インストーラーは`~/.local/bin/atk`（Windowsでは`atk.cmd`）へ最新版を動的に解決するラッパーを置く。
dotfiles導入では`chezmoi apply`が`~/dotfiles/agent-toolkit/scripts`をPATHへ加える。

## Codex向けの配布構成

dotfiles導入はCodex向けに次を配置する。
単体インストーラーは既存の`~/.codex/AGENTS.md`を保護するため、これらを配置しない。

- `~/.codex/AGENTS.md`: 全リポジトリへ適用する個人用の規範（`myprojects-common.md`）を`atk-auto`の境界付き本文として埋め込む（`scripts/sync_codex_agents.py`が生成）。
  agent-toolkitの規範は入口のスキル`agent-toolkit:atk-mode`と手順書が持ち、ここへは埋め込まない
- スキルなどの共有対象: リンクで配布する

chezmoiの`symlink_`はWindowsで特権不足により失敗するため、`chezmoi apply`後処理（`pytools.post_apply`）の専用ステップがリンクを生成する（Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション）。
Codexはプロジェクト直下の`.claude/rules/`と`.claude/skills/`を自動では読まない。
プロジェクト専用スキルは`.agents/skills -> .claude/skills`のシンボリックリンクで共有する。

プロジェクト規範は`AGENTS.md`を実体ファイルとし、`CLAUDE.md`は置かない（コピー欠落やシンボリックリンク非対応環境での障害を避けるため）。
Claude Codeは`CLAUDE.md`・`.claude/CLAUDE.md`・`CLAUDE.local.md`のいずれかがあると`AGENTS.md`を読まない。
このため個人用の`CLAUDE.local.md`を置いたプロジェクトでは、`atk setup-project`が`@AGENTS.md`を取り込むだけの`CLAUDE.md`アダプターを置き、`.git/info/exclude`で追跡対象から外す。
