# Claude Code 設定管理

本リポジトリのClaude Code関連ファイルは、他のプロジェクトで利用するためのテンプレートも兼ねている。
配布するものは以下の2系統。

- ルール（`.chezmoi-source/dot_claude/rules/agent-toolkit/` 配下）— 全プロジェクトで読み込ませるコーディング規約・運用方針
- プラグイン（`plugins/` 配下）— 本リポジトリ自体をClaude CodeのPlugin Marketplaceとして登録することで配布する

このドキュメントにはdotfiles管理側の情報（配布方式・配布元・他プロジェクトへの組み込み方）のみ記述している。
配布されるルールやプラグインの内容については [docs/guide/claude-code-guide.md](claude-code-guide.md) を参照。

## 配布方式

### 自分用: dotfiles経由（chezmoi）

本dotfilesを導入している環境では、chezmoiが配布元ディレクトリを `~/.claude/rules/agent-toolkit/` へデプロイする。
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
dotfiles全体を入れずに `~/.claude/rules/agent-toolkit/` だけを配置できる。

想定用途はチームプロジェクトのセットアップ手順への組み込み。
配布先は配布元と常に完全一致するよう同期する
（ステージングディレクトリへ全ファイルをダウンロードしてから原子的リネームで差し替える）。
利用者による個別カスタマイズは想定しておらず、再実行のたびに旧内容は上書きされる。

本スクリプトはClaude Codeが先にインストールされている前提で動作する。
`claude` CLIが見つからない場合はエラー終了する（ルールのみ配置しても主要機能を担うプラグインが導入できないため）。
実行時に `agent-toolkit` プラグインをuser scopeへインストール・更新し、
併せて旧 `edit-guardrails` プラグインを自動アンインストールする。

### プロジェクトローカルへの配布: `claudize`

プロジェクトの `.claude/rules/agent-toolkit/` にルールファイルを直接置きたい場合向けに `claudize` コマンドを用意している。

```bash
# プロジェクトディレクトリで実行（配布）
cd ~/your-project
claudize

# 解除（配布済みファイルを削除）
claudize --clean
```

`claudize` は配布元ディレクトリを丸ごと配布先へコピーする単純同期方式で動作する。
旧 `agent-basics` ディレクトリが残っていれば自動で削除する。

## 配布元

### ルール

配布元: `.chezmoi-source/dot_claude/rules/agent-toolkit/`

rules側の配布対象は`agent.md`と`styles.md`の2ファイル。
両者の分割は人間の編集・レビュー時の見通し改善のみを目的としており、
配布先では両ファイルとも常時自動ロードされるためClaude Codeの挙動には影響しない。
言語別・場面特化型の指示は`agent-toolkit`プラグインのスキルが担う。

配布元ディレクトリがSSOT。`claudize` とchezmoi経由の配布はディレクトリを丸ごと同期するため、
ファイル追加・削除・リネーム時の追加作業は不要。
`install-claude.sh` / `install-claude.ps1` のみファイルリスト配列（`FILES` / `$files`）に手動で追従する必要がある
（ワンライナー依存を減らすためGitHub APIは使わない）。

ルールファイルは他のユーザーと共有される可能性があるため、個別ツール（`claudize` など）への依存を含めないように注意すること。

### プラグイン

- 配布元: `plugins/` 配下
- Marketplace定義: `.claude-plugin/marketplace.json`
- 依存: `uv` CLI（hookスクリプトを `uv run --script` 経由で実行するため）
