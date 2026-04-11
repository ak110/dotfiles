# Claude Code 設定管理

本リポジトリのClaude Code関連ファイルは、他のプロジェクトで利用するためのテンプレートも兼ねている。
配布するものは以下の2系統。

- ルール (`.chezmoi-source/dot_claude/rules/agent-basics/` 配下) — 全プロジェクトで読み込ませるコーディング規約・運用方針
- プラグイン (`plugins/` 配下) — 本リポジトリ自体をClaude CodeのPlugin Marketplaceとして登録することで配布する

このドキュメントにはdotfiles管理側の情報（配布方式・配布元・他プロジェクトへの組み込み方）のみ記述している。
配布されるルールやプラグインの内容については [docs/guide/claude-code-concept.md](claude-code-concept.md) を参照。

## 配布方式

### 自分用: dotfiles 経由 (chezmoi)

本dotfilesを導入している環境では、chezmoiが配布元ディレクトリを `~/.claude/rules/agent-basics/` へデプロイする。
配置されたルールは全プロジェクトで自動読み込みされ、プロジェクトごとの個別配布は不要。

更新: `update-dotfiles` (または `chezmoi apply`) で最新化される。

`agent-toolkit` プラグインもdotfilesリポジトリのproject scopeに `chezmoi apply` 後処理で自動インストールされる。
(`claude` と `uv` がPATHにある場合のみ動作する。実装は `pytools/_install_claude_plugins.py`)

### 他人に配布: `install-claude.sh` / `install-claude.ps1`

チームメンバーにルールを使ってもらいたい場合向けのワンライナーインストーラー。
dotfiles全体を入れずに `~/.claude/rules/agent-basics/` だけを配置できる。

想定用途はチームプロジェクトのセットアップ手順への組み込み。
再実行時は既存ファイルのfrontmatterを維持したままbodyのみ更新されるため、利用者によるカスタマイズは保持される。
差分が発生した場合のバックアップは `~/.claude/rules-backup/agent-basics-<timestamp>/` に作成される。
（Claude Codeが `~/.claude/rules/` 配下を再帰的に読み込む仕様のため、退避ファイルが読まれないよう `rules/` の外に逃がしている）

#### プロジェクトのセットアップ手順への記述例

````markdown
## Claude Code セットアップ

本プロジェクトの開発時は [ak110/dotfiles の Claude Code 設定 (ルール・プラグイン)](https://github.com/ak110/dotfiles/blob/master/docs/guide/claude-code-concept.md) の導入を推奨する。
中身やカスタマイズ方法はリンク先を参照。

### 前提条件

プロジェクト管理者が `.claude/settings.json` に以下を設定済みであること。

```json
{
  "extraKnownMarketplaces": {
    "ak110-dotfiles": {
      "source": { "source": "github", "repo": "ak110/dotfiles" }
    }
  },
  "enabledPlugins": { "agent-toolkit@ak110-dotfiles": true }
}
```

### ルール導入 (Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
```

### ルール導入 (Windows)

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
```

### プラグイン (共通)

`.claude/settings.json` の `enabledPlugins` と `extraKnownMarketplaces` が設定済みの場合、
開発者がフォルダをtrustした時にClaude Codeがプラグインのインストールを自動で提案する。
手動でのコマンド実行は不要。

非公式のPlugin Marketplaceはデフォルトで自動更新が無効のため、初回のみ手動で有効化する。

1. Claude Code 内で `/plugin` を実行
2. `Marketplaces` タブで `ak110-dotfiles` を選択
3. `Enable auto-update` を選択
````

[uv](https://docs.astral.sh/uv/) を前提にしていないプロジェクトの場合は、導入手順にuvのインストールも追記するとよい。

- Linux: `curl -fsSL https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### プロジェクトローカルへの配布: `claudize`

プロジェクトの `.claude/rules/agent-basics/` にルールファイルを直接置きたい場合向けに `claudize` コマンドを用意している。

```bash
# プロジェクトディレクトリで実行 (配布)
cd ~/your-project
claudize

# 解除 (配布済みファイルを削除)
claudize --clean
```

`claudize` は対象プロジェクトに該当言語のファイルが存在する場合のみ言語別ルールを配布する。

## 配布元

### ルール

配布元: `.chezmoi-source/dot_claude/rules/agent-basics/`

ファイル一覧は以下3箇所で重複管理している。ルールファイルを追加・削除・リネームする際はすべてを更新すること。

- `pytools/claudize.py` — `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES`
- `install-claude.sh` — `FILES` 配列
- `install-claude.ps1` — `$files` 配列

ルールファイルは他のユーザーと共有される可能性があるため、個別ツール (`claudize` など) への依存を含めないように注意すること。

### プラグイン

- 配布元: `plugins/` 配下
- Marketplace定義: `.claude-plugin/marketplace.json`
- 依存: `uv` CLI（hookスクリプトを `uv run --script` 経由で実行するため）

プラグインの内容（チェック内容など）は [docs/guide/claude-code-concept.md](claude-code-concept.md) を参照。
