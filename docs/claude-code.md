# Claude Code 設定管理

本リポジトリの Claude Code 関連ファイルは、他のプロジェクトで利用するためのテンプレートも兼ねている。
配布元は `.chezmoi-source/dot_claude/rules/agent-basics/` 配下のファイル群。
これらは他のユーザーと共有される可能性があるため、個別ツール (`claudize` など) への依存を含めないようにしている。
配布元の管理やツール固有の情報はこの文書に集約する。

## 配布方式

### 自分用: dotfiles 経由 (chezmoi)

本 dotfiles を導入している環境では、chezmoi が配布元ディレクトリを `~/.claude/rules/agent-basics/` へデプロイする。
配置されたルールは全プロジェクトで自動読み込みされ、プロジェクトごとの個別配布は不要。

更新: `update-dotfiles` (または `chezmoi apply`) で最新化される。

### 他人に配布: `install-claude.sh` / `install-claude.ps1`

チームメンバーにルールを使ってもらいたい場合向けのワンライナーインストーラー。
dotfiles 全体を入れずに `~/.claude/rules/agent-basics/` だけを配置できる。

想定用途はチームプロジェクトのセットアップ手順への組み込み。
各メンバーはインストール後、必要に応じて `paths` frontmatter などを編集してプロジェクト事情に合わせてカスタマイズできる。
再実行時は既存ファイルの frontmatter を維持したまま body のみ更新される。

差分が発生した場合のバックアップは `~/.claude/rules-backup/agent-basics-<timestamp>/` に作成される。
Claude Code が `~/.claude/rules/` 配下を再帰的に読み込む仕様のため、退避ファイルが読まれないよう `rules/` の外に逃がしている。

#### プロジェクトのセットアップ手順への記述例

```markdown
## Claude Code ルールのインストール

本プロジェクトでは、共通の Claude Code ルールを `~/.claude/rules/agent-basics/` に配置します。

### Linux / macOS

    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash

### Windows (cmd)

    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"

インストール後、`~/.claude/rules/agent-basics/` 配下のファイルを必要に応じて編集してください。
たとえば `paths` frontmatter を変更すれば、各ルールの適用範囲をプロジェクト事情に合わせて調整できます。
再実行しても既存ファイルの frontmatter は維持され、body のみ更新されます。

body に差分があった場合、旧ファイルは `~/.claude/rules-backup/agent-basics-<timestamp>/` に退避されます。
バックアップ先を `~/.claude/rules/` の外に置いているのは、Claude Code が `~/.claude/rules/` 配下を再帰的に読み込む仕様のため、退避先が同じツリー内にあると古いルールが読まれてしまうからです。
不要になったバックアップは適宜削除してください。
```

### プロジェクトローカルへの配布: `claudize`

プロジェクトの `.claude/rules/agent-basics/` にルールファイルを直接置きたい場合向けに `claudize` コマンドを残している。
例: 他者と作業するプロジェクトで、リポジトリ内に特定の言語ルールだけを共有したい場合。

```bash
# プロジェクトディレクトリで実行 (配布)
cd ~/your-project
claudize

# 解除 (配布済みファイルを削除)
claudize --clean
```

`claudize` は対象プロジェクトに該当言語のファイルが存在する場合のみ言語別ルールを配布する。

## ルールファイル一覧

- `agent.md`: 自動化すべき部分とユーザー確認すべき部分のバランス調整、コード品質の維持のためのルール (無条件ロード)
- `{言語}.md`: 言語固有のコーディング規約 (`paths` frontmatter で該当言語ファイル編集時のみロード)
- `{言語}-test.md`: 言語固有のテスト方針 (同上)
- `markdown.md`: Markdown 記述スタイル (`.md`/`.mdx` 編集時のみロード)
- `rules.md`: ルール作成時のガイドライン (`.claude/rules/` 編集時のみロード)
- `skills.md`: スキル作成時のガイドライン (`.claude/skills/` 編集時のみロード)

`agent.md` 以外は `paths` frontmatter で該当拡張子のファイルを読んだときのみロードされる。
セッション開始時のコンテキスト消費を抑えるための仕組みであり、プロジェクト単位の厳密な分離ではない (たとえば Python ファイルを編集すれば `python.md` はロードされる)。

CLAUDE.md はプロジェクト固有の情報を記述するファイルとして、ルール配布の管理対象外。
プロジェクトごとに手動で管理する (`/init` コマンドなどを活用するのも手)。

## 配布対象ファイル一覧の SSOT

ファイル一覧は以下3箇所で重複管理している。

- `pytools/claudize.py` — `_UNCONDITIONAL_RULES` / `_CONDITIONAL_RULES`
- `install-claude.sh` — `FILES` 配列
- `install-claude.ps1` — `$files` 配列

新しいルールファイルを追加する際は、これら3箇所すべてを更新すること。
