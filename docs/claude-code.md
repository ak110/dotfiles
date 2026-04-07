# Claude Code 設定管理

本リポジトリの Claude Code 関連ファイルは、他のプロジェクトで利用するためのテンプレートも兼ねている。
配布元は 2 系統ある。

- ルール: `.chezmoi-source/dot_claude/rules/agent-basics/` 配下のファイル群
- plugin: `plugins/` 配下の Claude Code plugin (現在は `edit-guardrails` のみ)

ルール群は他のユーザーと共有される可能性があるため、個別ツール (`claudize` など) への依存を含めないようにしている。
plugin 群は `.claude-plugin/marketplace.json` 経由で配布する。

このドキュメントは dotfiles 管理側の情報 (配布方式・SSOT・他プロジェクトへの組み込み方) に集約している。
配布されるルールやプラグインの**中身**については [docs/claude-code-concept.md](claude-code-concept.md) を参照。

## 配布方式

### 自分用: dotfiles 経由 (chezmoi)

本 dotfiles を導入している環境では、chezmoi が配布元ディレクトリを `~/.claude/rules/agent-basics/` へデプロイする。
配置されたルールは全プロジェクトで自動読み込みされ、プロジェクトごとの個別配布は不要。

更新: `update-dotfiles` (または `chezmoi apply`) で最新化される。

`edit-guardrails` プラグインも個人環境では `chezmoi apply` 後処理で自動インストールされる。
(`claude` と `uv` が PATH にあり未導入の場合のみ動作する。実装は `pytools/_install_claude_plugins.py`。)

### 他人に配布: `install-claude.sh` / `install-claude.ps1`

チームメンバーにルールを使ってもらいたい場合向けのワンライナーインストーラー。
dotfiles 全体を入れずに `~/.claude/rules/agent-basics/` だけを配置できる。

想定用途はチームプロジェクトのセットアップ手順への組み込み。
再実行時は既存ファイルの frontmatter を維持したまま body のみ更新されるため、利用者によるカスタマイズは保持される。
差分が発生した場合のバックアップは `~/.claude/rules-backup/agent-basics-<timestamp>/` に作成される。
(Claude Code が `~/.claude/rules/` 配下を再帰的に読み込む仕様のため、退避ファイルが読まれないよう `rules/` の外に逃がしている。)

#### プロジェクトのセットアップ手順への記述例

````markdown
## Claude Code セットアップ

本プロジェクトの開発時は [ak110/dotfiles の Claude Code 設定 (ルール・プラグイン)](https://github.com/ak110/dotfiles/blob/master/docs/claude-code-concept.md) の導入を推奨する。
中身やカスタマイズ方法はリンク先を参照。

### 導入手順 (Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
claude plugin marketplace add ak110/dotfiles
claude plugin install edit-guardrails@ak110-dotfiles
```

### 導入手順 (Windows)

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
claude plugin marketplace add ak110/dotfiles
claude plugin install edit-guardrails@ak110-dotfiles
```

プラグインは [uv](https://docs.astral.sh/uv/) に依存するため事前にインストールしておくこと。
````

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

### Claude Code plugin (edit-guardrails)

`plugins/edit-guardrails/` に、危険な編集を `PreToolUse` 段階で検出する plugin を同梱している。
配布元は本リポジトリ自身で、`.claude-plugin/marketplace.json` に登録済み。
`uv` CLI に依存する (hook スクリプトを `uv run --script` 経由で実行するため)。

## 配布対象ファイル一覧の SSOT

ファイル一覧は以下 3 箇所で重複管理している。

- `pytools/claudize.py` — `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES`
- `install-claude.sh` — `FILES` 配列
- `install-claude.ps1` — `$files` 配列

新しいルールファイルを追加する際は、これら 3 箇所すべてを更新すること。
