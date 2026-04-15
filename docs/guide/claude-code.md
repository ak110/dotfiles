# Claude Code 設定管理

本リポジトリのClaude Code関連ファイルは、他のプロジェクトで利用するためのテンプレートも兼ねている。
配布するものは以下の2系統。

- ルール（`.chezmoi-source/dot_claude/rules/agent-basics/` 配下）— 全プロジェクトで読み込ませるコーディング規約・運用方針
- プラグイン（`plugins/` 配下）— 本リポジトリ自体をClaude CodeのPlugin Marketplaceとして登録することで配布する

このドキュメントにはdotfiles管理側の情報（配布方式・配布元・他プロジェクトへの組み込み方）のみ記述している。
配布されるルールやプラグインの内容については [docs/guide/claude-code-guide.md](claude-code-guide.md) を参照。

## 配布方式

### 自分用: dotfiles経由（chezmoi）

本dotfilesを導入している環境では、chezmoiが配布元ディレクトリを `~/.claude/rules/agent-basics/` へデプロイする。
配置されたルールは全プロジェクトで自動読み込みされ、プロジェクトごとの個別配布は不要。

更新: `update-dotfiles`（または `chezmoi apply`）で最新化される。

`agent-toolkit` プラグインもuser scopeに `chezmoi apply` 後処理で自動インストールされる。
（`claude` と `uv` がPATHにある場合のみ動作する。実装は `pytools/_install_claude_plugins.py`）
user scope化により全プロジェクトで共通に有効化される。
プロジェクトごとの `.claude/settings.json` への記述は不要になる。

codex MCPサーバー（`codex mcp-server`）も同様に `chezmoi apply` 後処理でuser scopeへ自動登録される。
（`claude` CLIがPATHにある場合のみ動作する。実装は `pytools/_install_codex_mcp.py`）
登録済みの場合は冪等にスキップする。codex CLI自体のセットアップは別途行うこと。

### 他人に配布: `install-claude.sh` / `install-claude.ps1`

チームメンバーにルールを使ってもらいたい場合向けのワンライナーインストーラー。
dotfiles全体を入れずに `~/.claude/rules/agent-basics/` だけを配置できる。

想定用途はチームプロジェクトのセットアップ手順への組み込み。
再実行時は既存ファイルのfrontmatterを維持したままbodyのみ更新されるため、利用者によるカスタマイズは保持される。
差分が発生した場合のバックアップは `~/.claude/rules-backup/agent-basics-<timestamp>/` に作成される。
（Claude Codeが `~/.claude/rules/` 配下を再帰的に読み込む仕様のため、退避ファイルは `rules/` の外にしている）

本スクリプトは実行時に `claude` CLIを検出した場合、`agent-toolkit` プラグインもuser scopeに自動インストールする。
言語別ルール・計画モード・バグ対応・Claude設定記述ガイドはプラグイン側のスキルが提供するため、プラグイン導入が前提となる。
CLI未導入の環境では案内メッセージのみ表示する。
後から手動で以下のコマンドを実行する運用を想定している。

```bash
claude plugin marketplace add ak110/dotfiles
claude plugin install agent-toolkit@ak110-dotfiles --scope user
```

### プロジェクトローカルへの配布: `claudize`

プロジェクトの `.claude/rules/agent-basics/` にルールファイルを直接置きたい場合向けに `claudize` コマンドを用意している。

```bash
# プロジェクトディレクトリで実行（配布）
cd ~/your-project
claudize

# 解除（配布済みファイルを削除）
claudize --clean
```

`claudize` は拡張子に応じた条件付き配布の仕組みを持つが、現在の配布対象は共通ルール（`agent.md`・`markdown.md`）のみで、条件付きで配布されるルールは設定されていない。

## 配布元

### ルール

配布元: `.chezmoi-source/dot_claude/rules/agent-basics/`

rules側の配布対象は `agent.md` と `markdown.md` の2ファイル。
言語別・場面特化型の指示は `agent-toolkit` プラグインのスキルが担う。

ファイル一覧は以下3箇所で重複管理している。ルールファイルを追加・削除・リネームする際はすべてを更新すること。
旧配布対象ファイルの削除は、各ファイルのobsoleteリストで追跡している（再実行時の既存環境クリーンアップ用）。

- `pytools/claudize.py` — `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES` / `_OBSOLETE_RULES`
- `install-claude.sh` — `FILES` 配列 / `OBSOLETE_FILES` 配列
- `install-claude.ps1` — `$files` 配列 / `$obsoleteFiles` 配列
- `pytools/post_apply.py` — `_REMOVED_PATHS` に `rules/agent-basics/<旧ファイル>.md` を列挙

ルールファイルは他のユーザーと共有される可能性があるため、個別ツール（`claudize` など）への依存を含めないように注意すること。

### プラグイン

- 配布元: `plugins/` 配下
- Marketplace定義: `.claude-plugin/marketplace.json`
- 依存: `uv` CLI（hookスクリプトを `uv run --script` 経由で実行するため）
